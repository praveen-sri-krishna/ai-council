"""Turn a debate into a complete, presentable document — and keep a library of them.

The leader (best long-form writer) writes a polished markdown doc from the full
debate context, saved under library/ so you can pull it out anytime.
"""
import json
import re
from pathlib import Path

from leader import _seat_by_id, load_session
from seats import call_seat, load_config

LIBRARY = Path("library")
LIBRARY.mkdir(exist_ok=True)


def _slug(text: str, n: int = 8) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:n]
    return "-".join(words) or "idea"


def _writer_seat(cfg, session: dict):
    """Prefer the chair (gemini, 1M context, strong prose); else the debate's leader."""
    return (_seat_by_id(cfg, "gemini-chair")
            or _seat_by_id(cfg, session.get("final", {}).get("best_model", ""))
            or cfg.seats[0])


def generate_document(session_path: str, cfg=None) -> tuple[str, str]:
    """Write a presentable document for a debate. Returns (file_path, markdown)."""
    cfg = cfg or load_config()
    s = load_session(session_path)
    final = s.get("final", {})
    writer = _writer_seat(cfg, s)
    charter = cfg.defaults.get("charter", "")

    context = json.dumps({
        "question": s.get("prompt"),
        "verdict": final.get("verdict"),
        "direct_answer": final.get("direct_answer"),
        "key_points": final.get("key_points") or final.get("ranked_plan"),
        "differentiators": final.get("differentiators"),
        "competitors": final.get("competitors"),
        "builds_on": final.get("builds_on"),
        "evidence": [{"verdict": v.get("verdict"), "claim": v.get("claim")}
                     for v in final.get("verdicts", [])],
        "minority_report": final.get("minority_report"),
        "dissent": final.get("dissent"),
        "confidence": final.get("confidence"),
    }, indent=2, ensure_ascii=False)[:14000]

    system = (
        f"{charter}\n\n"
        "You are the council's lead author. Turn this debate into a COMPLETE, presentable document "
        "someone can use to build, pitch, or decide on the idea. Clean markdown, professional, "
        "concrete, no buzzwords. Use exactly these sections:\n"
        "# <punchy title>\n"
        "## Verdict — lead with pursue/pivot/drop + the odds, in one paragraph\n"
        "## Executive Summary — the idea and the answer in a few sentences\n"
        "## The Opportunity — why now, who it's for, who pays\n"
        "## The Idea — what it is, concretely\n"
        "## Why It Beats What Exists — name each real competitor, their gap, and our differentiator\n"
        "## Plan / Key Steps\n"
        "## Risks & Open Questions — include the minority report honestly\n"
        "## Recommended Next Steps\n"
        "Return ONLY the markdown document."
    )
    md = call_seat(writer, [{"role": "system", "content": system},
                            {"role": "user", "content": context}], cfg.defaults,
                   max_tokens=8000).strip()
    # strip a stray code fence if the model wrapped the whole doc
    if md.startswith("```"):
        md = re.sub(r"^```[a-z]*\n?|\n?```$", "", md).strip()

    stamp = Path(session_path).stem.replace("session_", "")
    title = (final.get("title") or "").strip() or s.get("prompt", "")
    path = LIBRARY / f"{_slug(title)}_{stamp}.md"
    path.write_text(md, encoding="utf-8")
    return str(path), md


def list_documents() -> list[tuple[str, str]]:
    """(readable label, path) for every saved document, newest first."""
    out = []
    for p in sorted(LIBRARY.glob("*.md"), reverse=True):
        title = ""
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        except Exception:
            pass
        out.append((title or p.stem, str(p)))
    return out
