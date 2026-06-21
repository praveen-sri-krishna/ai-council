"""Memory: the within-debate Memory Palace (JSON state) + cross-debate case
memory via the installed Memento MCP server.

Memento is a local-first SQLite store (no network), so case memory works even in
privacy_mode local_only. We speak MCP over stdio to the installed `memento-mcp`.
All Memento calls degrade gracefully: if node/the server is unavailable, a debate
still runs, just without past-case recall.
"""
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

_MEMENTO_MAIN = (r"C:\Users\Player 1\AppData\Local\hermes\node\node_modules"
                 r"\@luispmonteiro\memento-memory-mcp\dist\cli\main.js")
_PROJECT_PATH = str(Path(__file__).parent)


def _memento_call(tool: str, arguments: dict):
    """One MCP round-trip to memento-mcp: spawn, handshake, call one tool, close.
    Returns the tool's text result, or None on any failure (never raises)."""
    try:
        p = subprocess.Popen(
            ["node", _MEMENTO_MAIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
    except Exception:
        return None

    def send(obj):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()

    def read_id(want):
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == want:
                return msg
        return None

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "ai-council", "version": "0.1"}}})
        if not read_id(1):
            return None
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": tool, "arguments": arguments}})
        resp = read_id(2)
        if not resp or "result" not in resp:
            return None
        parts = resp["result"].get("content", [])
        return "\n".join(x.get("text", "") for x in parts if x.get("type") == "text")
    except Exception:
        return None
    finally:
        try:
            p.stdin.close()
            p.terminate()
        except Exception:
            pass


def retrieve_cases(idea: str, limit: int = 3) -> list[str]:
    """Top-k similar past debates, as text summaries to inject into proposals."""
    out = _memento_call("memory_search", {
        "query": idea, "limit": limit, "detail": "summary",
        "memory_type": "council_case", "project_path": _PROJECT_PATH})
    return [out] if out else []


def write_case(mp: "MemoryPalace", limit_plan: int = 6) -> bool:
    """Persist this debate as a Memento case so future debates can learn from it."""
    final = mp.final or {}
    plan = final.get("ranked_plan", [])[:limit_plan]
    body = {
        "idea": mp.prompt,
        "mode": final.get("mode"),
        "verdict": final.get("verdict", {}),
        "direct_answer": final.get("direct_answer", ""),
        "ranked_plan": plan,
        "builds_on": final.get("builds_on", []),
        "confidence": final.get("confidence"),
        "evidence_ratio": final.get("evidence_ratio"),
        "minority_report": final.get("minority_report", {}).get("reason", ""),
        "best_model": final.get("best_model"),
    }
    res = _memento_call("memory_store", {
        "title": f"Council case: {mp.prompt[:80]}",
        "content": "```json\n" + json.dumps(body, indent=2, ensure_ascii=False) + "\n```",
        "memory_type": "council_case",
        "scope": "project",
        "project_path": _PROJECT_PATH,
        "tags": ["ai-council", "debate"]})
    return res is not None


@dataclass
class MemoryPalace:
    prompt: str
    research: dict = field(default_factory=dict)       # {queries, prior_art, verdicts, evidence_ratio}
    proposals: dict = field(default_factory=dict)     # seat_id -> {role, proposal, reasoning, claims}
    critiques: dict = field(default_factory=dict)     # critic_id -> {target_id -> {...}}
    synthesis: list = field(default_factory=list)     # one entry per synthesize->vote round
    discussion: list = field(default_factory=list)    # running log of phase events
    final: dict = field(default_factory=dict)         # best synthesis + minority report

    def log(self, speaker: str, content: str) -> None:
        self.discussion.append({"speaker": speaker, "content": content})

    def add_proposal(self, seat_id: str, role: str, data: dict) -> None:
        self.proposals[seat_id] = {"role": role, **data}

    def add_critique(self, critic_id: str, data: dict) -> None:
        self.critiques[critic_id] = data

    def add_synthesis_round(self, proposal: dict, votes: dict,
                            avg: float, adjusted: float) -> None:
        self.synthesis.append(
            {"proposal": proposal, "votes": votes, "avg": avg, "adjusted": adjusted}
        )

    def maybe_compress(self, limit: int, summarizer) -> None:
        """Compress the discussion log once it grows past `limit` entries.
        `summarizer(text) -> str` collapses old entries to a single summary."""
        if len(self.discussion) <= limit:
            return
        old = self.discussion[:-3]
        keep = self.discussion[-3:]
        text = "\n".join(f"[{e['speaker']}] {e['content']}" for e in old)
        summary = summarizer(text)
        self.discussion = [{"speaker": "summary", "content": summary}] + keep

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.to_json(), encoding="utf-8")
