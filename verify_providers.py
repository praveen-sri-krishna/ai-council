"""Stdlib-only probe: validate every provider key and capture live model strings.
No deps required (uses urllib). Run: python verify_providers.py"""
import json
import os
import urllib.request
import urllib.error
from pathlib import Path


def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def get_json(url: str, headers: dict, timeout: int = 20):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:200]
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def model_ids(payload) -> list:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [m.get("id", "?") for m in payload["data"]]
    if isinstance(payload, dict) and isinstance(payload.get("models"), list):
        return [m.get("name", m.get("id", "?")) for m in payload["models"]]
    return []


def show(name: str, status, payload, want: list[str]) -> None:
    ok = status == 200
    mark = "OK " if ok else "FAIL"
    print(f"\n[{mark}] {name}  (HTTP {status})")
    if not ok:
        print(f"      -> {payload}")
        return
    ids = model_ids(payload)
    print(f"      {len(ids)} models available")
    for w in want:
        hits = [m for m in ids if w.lower() in m.lower()]
        if hits:
            print(f"      match '{w}': {hits[:4]}")


def main() -> None:
    load_env()
    print("=" * 60)
    print("AI COUNCIL — PROVIDER VERIFICATION")
    print("=" * 60)

    # Local Ollama
    s, p = get_json("http://localhost:11434/api/tags", {})
    show("Ollama (local)", s, p, ["qwen3", "deepseek-r1", "gemma"])

    probes = [
        ("Groq", "https://api.groq.com/openai/v1/models", "GROQ_API_KEY",
         ["llama", "qwen", "gpt-oss", "kimi"]),
        ("NVIDIA NIM", "https://integrate.api.nvidia.com/v1/models", "NVIDIA_API_KEY",
         ["llama", "qwen", "deepseek", "nemotron"]),
        ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/models",
         "GEMINI_API_KEY", ["gemini-2", "flash"]),
        ("OpenRouter", "https://openrouter.ai/api/v1/models", "OPENROUTER_API_KEY",
         ["kimi", "free", "deepseek"]),
        ("Cerebras", "https://api.cerebras.ai/v1/models", "CEREBRAS_API_KEY",
         ["llama", "qwen", "gpt-oss"]),
    ]
    for name, url, env_key, want in probes:
        key = os.environ.get(env_key, "")
        if not key:
            print(f"\n[SKIP] {name} — {env_key} not set")
            continue
        s, p = get_json(url, {"Authorization": f"Bearer {key}"})
        show(name, s, p, want)

    print("\n" + "=" * 60)
    print("Done. FAIL lines = bad/expired key or wrong endpoint.")


if __name__ == "__main__":
    main()
