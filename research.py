"""Web grounding: keyless search (DuckDuckGo) + GitHub prior-art, file-cached.

Per the brief: two passes per claim (supporting AND counter) so the panel can't
all agree on something false. Provider is swappable -- SearXNG can replace
DuckDuckGo later behind the same search_web() signature. Caching avoids
re-spending quota on a repeated query.
"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_CACHE_PATH = Path("sessions/.search_cache.json")


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


_CACHE = _load_cache()


def _cached(key: str, fn):
    if key in _CACHE:
        return _CACHE[key]
    val = fn()
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


def _github_once(query: str, max_results: int) -> list[dict]:
    url = ("https://api.github.com/search/repositories?q="
           + urllib.parse.quote(query) + f"&sort=stars&per_page={max_results}")
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    return [{"repo": it["full_name"], "stars": it["stargazers_count"],
             "url": it["html_url"], "desc": (it.get("description") or "")[:200]}
            for it in d.get("items", [])[:max_results]]


def github_prior_art(query: str, max_results: int = 5) -> list[dict]:
    """What already exists on GitHub for this idea -- so the council builds on
    proven work instead of reinventing it. GitHub ANDs all terms, so a long
    query matches nothing; we progressively drop terms until results appear."""
    def run() -> list[dict]:
        terms = query.split()
        while terms:
            hits = _github_once(" ".join(terms), max_results)
            if hits:
                return hits
            terms = terms[:-1]  # relax: drop the most specific (last) term
        return []
    try:
        return _cached(f"gh::{query}", run)
    except Exception:
        return []


def gather_evidence(claim: str, max_results: int = 4, provider: str = "duckduckgo") -> dict:
    """Adversarial retrieval for one claim: a supporting pass and a counter pass."""
    supporting = search_web(claim, max_results, provider)
    counter = search_web(f"{claim} criticism OR limitations OR debunked OR false",
                         max_results, provider)
    return {"claim": claim, "supporting": supporting, "counter": counter}
