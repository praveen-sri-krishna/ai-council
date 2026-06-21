"""Web grounding: keyless search (DuckDuckGo) + GitHub prior-art, file-cached.

Per the brief: two passes per claim (supporting AND counter) so the panel can't
all agree on something false. Provider is swappable -- SearXNG can replace
DuckDuckGo later behind the same search_web() signature. Caching avoids
re-spending quota on a repeated query.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_CACHE_PATH = Path("sessions/.search_cache.json")
# Authenticated GitHub search = 30 req/min (vs 10 unauth) -> far fewer rate-limit []s.
_GH_TOKEN = os.environ.get("Github_API_KEY") or os.environ.get("GITHUB_TOKEN") or ""
_STOP = {"recommend", "architecture", "design", "build", "create", "with", "that",
         "this", "without", "across", "their", "for", "the", "and", "app", "using",
         "use", "how", "should", "small", "teams", "team", "best", "system"}


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


_CACHE = _load_cache()


def _cached(key: str, fn):
    if _CACHE.get(key):  # only trust non-empty cache hits
        return _CACHE[key]
    val = fn()
    if val:  # never cache empty results -> a transient 0 won't stick across runs
        _CACHE[key] = val
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(_CACHE, ensure_ascii=False), encoding="utf-8")
    return val


def search_web(query: str, max_results: int = 5, provider: str = "duckduckgo") -> list[dict]:
    """Keyless web search. Returns [{title, url, snippet}]. Empty list on failure
    (so a search outage degrades to 'uncertain', never crashes a debate)."""
    def run() -> list[dict]:
        if provider == "duckduckgo":
            from ddgs import DDGS
            out = []
            for r in DDGS().text(query, max_results=max_results):
                out.append({"title": r.get("title", ""), "url": r.get("href", ""),
                            "snippet": r.get("body", "")})
            return out
        if provider == "searxng":
            # Phase 3+: query a local SearXNG JSON endpoint when Docker is up.
            return []
        return []
    try:
        return _cached(f"web::{provider}::{query}", run)
    except Exception:
        return []


def _github_once(query: str, max_results: int, sort: str = "stars") -> list[dict]:
    # sort=stars -> most proven; sort=updated -> most recently active (latest/trending)
    extra = f"&sort={sort}" if sort in ("stars", "updated", "forks") else ""
    url = ("https://api.github.com/search/repositories?q="
           + urllib.parse.quote(query) + f"{extra}&per_page={max_results}")
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if _GH_TOKEN:
        headers["Authorization"] = f"Bearer {_GH_TOKEN}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
        d = json.loads(r.read())
    return [{"repo": it["full_name"], "stars": it["stargazers_count"],
             "url": it["html_url"], "desc": (it.get("description") or "")[:200],
             "updated": (it.get("pushed_at") or "")[:10]}
            for it in d.get("items", [])[:max_results]]


def _keywords(query: str, cap: int = 5) -> list[str]:
    """Distil a query to its few most meaningful terms. GitHub ANDs all terms, so
    a long sentence matches nothing -- and one call per word blows the rate limit."""
    terms = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", query)
             if w.lower() not in _STOP]
    return terms[:cap] or re.findall(r"[A-Za-z]{3,}", query)[:3]


def github_prior_art(query: str, max_results: int = 5, sort: str = "stars") -> list[dict]:
    """What exists on GitHub for this idea. sort=stars = most proven; sort=updated =
    latest/trending. Distil to <=5 keywords, relax to 2 (bounded ~4 calls, authed)."""
    def run() -> list[dict]:
        terms = _keywords(query)
        while len(terms) >= 2:
            hits = _github_once(" ".join(terms), max_results, sort)
            if hits:
                return hits
            terms = terms[:-1]
        return _github_once(terms[0], max_results, sort) if terms else []
    try:
        return _cached(f"gh::{sort}::{query}", run)
    except Exception:
        return []


def gather_evidence(claim: str, max_results: int = 4, provider: str = "duckduckgo") -> dict:
    """Adversarial retrieval for one claim: a supporting pass and a counter pass."""
    supporting = search_web(claim, max_results, provider)
    counter = search_web(f"{claim} criticism OR limitations OR debunked OR false",
                         max_results, provider)
    return {"claim": claim, "supporting": supporting, "counter": counter}
