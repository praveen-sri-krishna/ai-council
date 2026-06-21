"""One-off: give existing sessions a clean core-idea title (new debates get one
natively). For each session missing final.title, ask a fast seat for a short title."""
import json
from pathlib import Path

from seats import call_seat, extract_json, load_config

cfg = load_config()
seat = next((s for s in cfg.seats if s.id == "cerebras-fast"), cfg.seats[0])

for p in sorted(Path("sessions").glob("session_*.json")):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    final = d.get("final") or {}
    if final.get("title"):
        continue
    prompt = d.get("prompt", "")
    da = final.get("direct_answer", "")
    sys = ('Give a 3-7 word title capturing the CORE IDEA (not the opening words). '
           'ONLY JSON: {"title": ""}')
    try:
        txt = call_seat(seat, [{"role": "system", "content": sys},
                               {"role": "user", "content": f"Question: {prompt}\nAnswer: {da[:300]}"}],
                        cfg.defaults, max_tokens=400)
        title = (extract_json(txt) or {}).get("title", "").strip()
    except Exception as e:
        title = ""
    if title:
        final["title"] = title
        d["final"] = final
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        print(f"{p.name}: {title}")
    else:
        print(f"{p.name}: (no title generated)")
