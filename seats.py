"""Seats + the single OpenAI-compatible adapter every provider goes through.

call_seat() is the one and only model-call path: local Ollama and all hosted
providers differ only by base_url / model / key_env. Keep it that way.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import (APIConnectionError, APIStatusError, APITimeoutError, OpenAI,
                    RateLimitError)

load_dotenv()


def _retryable(exc: Exception) -> bool:
    """Transient failures worth a backoff+retry: rate limits, 5xx, timeouts."""
    if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return False


def _retry_delay(exc: Exception, attempt: int, base: float, cap: float) -> float:
    """Honor Retry-After when the provider sends it; else exponential backoff."""
    try:
        if isinstance(exc, RateLimitError) and exc.response is not None:
            ra = exc.response.headers.get("retry-after")
            if ra:
                return min(float(ra), cap)
    except Exception:
        pass
    return min(base * (2 ** attempt), cap)


@dataclass
class Seat:
    id: str
    provider: str
    base_url: str
    model: str
    role: str
    personality: str = ""
    key_env: str | None = None
    local: bool = False
    keep_alive: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None   # hosted thinking models: "none"|"low"|"medium"|"high"


@dataclass
class Config:
    privacy_mode: str
    consensus_threshold: float
    max_iterations: int
    history_compress_after: int
    defaults: dict
    search_provider: str = "duckduckgo"
    seats: list[Seat] = field(default_factory=list)
    failover: dict = field(default_factory=dict)


def load_config(path: str = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    seats = [Seat(**s) for s in raw.get("seats", [])]
    return Config(
        privacy_mode=raw.get("privacy_mode", "open"),
        consensus_threshold=raw.get("consensus_threshold", 0.95),
        max_iterations=raw.get("max_iterations", 4),
        history_compress_after=raw.get("history_compress_after", 15),
        defaults=raw.get("defaults", {}),
        search_provider=raw.get("search_provider", "duckduckgo"),
        seats=seats,
        failover=raw.get("failover", {}),
    )


def available_seats(cfg: Config) -> list[Seat]:
    """Drop seats we can't run: hosted seats with a missing key, or any hosted
    seat at all when privacy_mode is local_only. Never crash on a missing key."""
    out = []
    for s in cfg.seats:
        if s.local:
            out.append(s)
            continue
        if cfg.privacy_mode == "local_only":
            continue
        if s.key_env and not os.environ.get(s.key_env):
            continue
        out.append(s)
    return out


def _client(seat: Seat, defaults: dict) -> OpenAI:
    api_key = "ollama"
    if seat.key_env:
        api_key = os.environ.get(seat.key_env, "") or "missing"
    return OpenAI(
        base_url=seat.base_url,
        api_key=api_key,
        timeout=defaults.get("timeout", 120),
        default_headers={"User-Agent": defaults.get("user_agent", "")},
        max_retries=0,  # we own retry/failover policy (Phase 5), not the SDK
    )


def _resolve_temp(seat: Seat, defaults: dict, temperature: float | None) -> float:
    if temperature is not None:
        return temperature
    if seat.temperature is not None:
        return seat.temperature
    return defaults.get("temperature", 0.7)


def _call_ollama_native(seat: Seat, messages: list[dict], defaults: dict,
                        max_tokens: int, temperature: float) -> str:
    """Local seats use Ollama's native /api/chat: the OpenAI-compat endpoint
    ignores num_ctx, which leaves the model at its 128K default (35GB -> CPU).
    Capping num_ctx here keeps the model 100% on GPU."""
    base = seat.base_url.rsplit("/v1", 1)[0]
    body = json.dumps({
        "model": seat.model,
        "messages": messages,
        "stream": False,
        "keep_alive": seat.keep_alive if seat.keep_alive is not None else 0,
        "options": {
            "num_ctx": defaults.get("num_ctx", 8192),
            "num_predict": max_tokens,
            "temperature": temperature,
        },
    }).encode()
    req = urllib.request.Request(base + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=defaults.get("timeout", 600)) as r:
        data = json.loads(r.read())
    msg = data.get("message", {}) or {}
    return (msg.get("content") or msg.get("thinking") or "").strip()


def _once(seat: Seat, messages: list[dict], defaults: dict, mt: int, temp: float) -> str:
    if seat.local:
        return _call_ollama_native(seat, messages, defaults, mt, temp)
    kwargs: dict = {"model": seat.model, "messages": messages,
                    "max_tokens": mt, "temperature": temp}
    if seat.reasoning_effort:  # cap thinking on hosted reasoners so the answer isn't truncated
        kwargs["reasoning_effort"] = seat.reasoning_effort
    resp = _client(seat, defaults).chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    text = (msg.content or "").strip()
    if not text:
        for attr in ("reasoning_content", "reasoning"):
            val = getattr(msg, attr, None)
            if val:
                text = str(val).strip()
                break
    return text


def call_seat(seat: Seat, messages: list[dict], defaults: dict,
              max_tokens: int | None = None, temperature: float | None = None) -> str:
    """The single model-call path, with retry+backoff so a debate never dies on a
    rate limit. Local Ollama goes through the native API (to cap context); hosted
    providers go through the OpenAI SDK. Returns text (never None)."""
    mt = max_tokens or defaults.get("max_tokens", 2048)
    temp = _resolve_temp(seat, defaults, temperature)
    retries = int(defaults.get("max_retries", 3))
    base = float(defaults.get("retry_base", 2.0))
    cap = float(defaults.get("retry_cap", 30.0))

    for attempt in range(retries + 1):
        try:
            return _once(seat, messages, defaults, mt, temp)
        except Exception as exc:  # noqa: BLE001 - we re-raise non-transient below
            if attempt < retries and _retryable(exc):
                time.sleep(_retry_delay(exc, attempt, base, cap))
                continue
            raise


def extract_json(text: str):
    """Tolerant JSON recovery: strip code fences, try direct parse, then take the
    outermost balanced {...} / [...] block (handles nesting). None if nothing parses."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip() if "```" in text else text.strip()
    for cand in (cleaned, text.strip()):
        try:
            return json.loads(cand)
        except Exception:
            pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(open_c), cleaned.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except Exception:
                continue
    return None
