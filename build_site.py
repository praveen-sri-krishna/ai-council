"""Generate a static GitHub Pages site (docs/index.html) that showcases a real
council debate -- the live app needs a Python backend, but this static page works
anywhere (mobile, no server). Reads demo/*.json and bakes the content in."""
import html
import json
from pathlib import Path

DEMO = json.loads(Path("demo/phase3_demo.json").read_text(encoding="utf-8"))
EVAL = json.loads(Path("demo/eval_demo_result.json").read_text(encoding="utf-8"))
FINAL = DEMO.get("final", {})
VC = {"supported": "#3ecf8e", "refuted": "#ff5d5d", "uncertain": "#ffb000"}


def e(x) -> str:
    return html.escape(str(x))


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=JetBrains+Mono:wght@400;600&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
:root{--ink:#0c0f13;--panel:#141a21;--panel2:#1b232d;--amber:#ffb000;--amber2:#ff8a00;
--text:#e7ebf0;--muted:#8a94a3;--green:#3ecf8e;--red:#ff5d5d;--line:#283441}
body{background:var(--ink);color:var(--text);font-family:'JetBrains Mono',monospace;
line-height:1.55;padding:0 18px 80px}
.wrap{max-width:920px;margin:0 auto}
header{border-bottom:1px solid var(--line);padding:46px 0 30px;margin-bottom:30px}
h1{font-family:'Fraunces',serif;font-weight:700;font-size:clamp(30px,7vw,52px);letter-spacing:-1px;line-height:1}
h1 .ac{color:var(--amber)}
.tag{color:var(--muted);margin-top:12px;font-size:14px;max-width:640px}
.idea{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--amber);
border-radius:0 10px 10px 0;padding:16px 18px;margin-top:24px;font-size:15px}
.idea b{color:var(--amber);font-family:'Fraunces',serif;font-size:11px;letter-spacing:2px;
text-transform:uppercase;display:block;margin-bottom:6px}
.stats{display:flex;flex-wrap:wrap;gap:12px;margin-top:24px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 18px;flex:1;min-width:140px}
.stat .n{font-family:'Fraunces',serif;font-size:30px;font-weight:700;color:var(--amber);line-height:1}
.stat .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:1.5px;margin-top:6px}
section{margin-top:44px}
h2{font-family:'Fraunces',serif;color:var(--amber);font-size:20px;margin-bottom:16px;
border-bottom:1px solid var(--line);padding-bottom:8px}
.rounds{display:flex;gap:8px;flex-wrap:wrap}
.rp{background:var(--panel2);border:1px solid var(--line);border-radius:20px;padding:5px 13px;font-size:13px}
.rp b{color:var(--amber)} .rp.win{border-color:var(--green)} .rp.win b{color:var(--green)}
ol{padding-left:22px} ol li{margin-bottom:12px}
.fix{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:12px 14px;margin-bottom:10px}
.fix .o{color:var(--red);font-size:13px} .fix .s{color:var(--green);font-size:13px;margin-top:5px}
.ev{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:11px 13px;margin-bottom:9px;font-size:13.5px}
.chip{display:inline-block;font-size:11px;font-weight:600;padding:2px 9px;border-radius:5px;color:#0c0f13;margin-right:8px}
.note{color:var(--muted);font-size:12px;margin-top:4px}
.lb{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.lb .nm{width:160px;font-size:13.5px} .lb .nm .cr{color:var(--amber)}
.lb .bar{height:12px;background:linear-gradient(90deg,var(--amber2),var(--amber));border-radius:6px}
.tags span{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:20px;
padding:3px 12px;font-size:12.5px;margin:0 6px 8px 0}
.mr{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--red);
border-radius:0 9px 9px 0;padding:13px 15px;font-size:13.5px}
.vs{display:flex;gap:14px;align-items:flex-end;margin-top:6px}
.vs .col{flex:1;text-align:center}
.vs .bar{background:var(--panel2);border-radius:8px 8px 0 0;margin-top:8px}
.vs .bar.win{background:linear-gradient(180deg,var(--amber),var(--amber2))}
.vs .sc{font-family:'Fraunces',serif;font-size:26px;font-weight:700}
footer{margin-top:60px;border-top:1px solid var(--line);padding-top:24px;color:var(--muted);font-size:13px}
a{color:var(--amber)}
.run{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px 16px;margin-top:14px;
font-size:13px;white-space:pre-wrap;color:var(--text)}
"""


def render() -> str:
    rounds = DEMO.get("synthesis", [])
    thr = 0.95
    rps = "".join(
        f"<span class='rp{' win' if r['adjusted'] >= thr else ''}'>R{i+1} <b>{r['adjusted']:.3f}</b></span>"
        for i, r in enumerate(rounds))
    plan = "".join(f"<li>{e(p)}</li>" for p in FINAL.get("ranked_plan", []))
    fixes = "".join(f"<div class='fix'><div class='o'>&#9888; {e(f.get('objection',''))}</div>"
                    f"<div class='s'>&#10003; {e(f.get('solution',''))}</div></div>"
                    for f in FINAL.get("fixes", []))
    verds = "".join(
        f"<div class='ev'><span class='chip' style='background:{VC.get(v.get('verdict'),'#888')}'>"
        f"{e(v.get('verdict'))}</span>{e(v.get('claim',''))}"
        f"<div class='note'>{e(v.get('note',''))}</div></div>"
        for v in FINAL.get("verdicts", []))
    lb = FINAL.get("leaderboard", {})
    best = FINAL.get("best_model")
    maxs = max(lb.values()) if lb else 1
    lbrows = "".join(
        f"<div class='lb'><div class='nm'>{'<span class=cr>' if k==best else ''}{e(k)}"
        f"{' &#9818;' if k==best else ''}{'</span>' if k==best else ''}</div>"
        f"<div class='bar' style='width:{int(s/maxs*420)}px'></div><div class='note'>{s}</div></div>"
        for k, s in lb.items())
    tools = "".join(f"<span>{e(t)}</span>" for t in FINAL.get("builds_on", []))
    mr = FINAL.get("minority_report", {})
    c = FINAL.get("evidence_counts", {})

    sc = EVAL.get("scores", {})
    st = sc.get("single", {}).get("total", 0) or 0
    ct = sc.get("council", {}).get("total", 0) or 0
    mx = max(st, ct, 1)

    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>AI Council — Situation Room</title><style>{CSS}</style></head><body><div class=wrap>
<header><h1>AI <span class=ac>Council</span></h1>
<p class=tag>A local-first, multi-model think tank. A panel of LLMs propose, critique
each other, web-check claims, and converge on one ranked plan &mdash; with a minority report.
This page shows a real recorded debate.</p>
<div class=idea><b>The problem put to the council</b>{e(DEMO.get('prompt',''))}</div>
<div class=stats>
  <div class=stat><div class=n>{FINAL.get('confidence')}</div><div class=l>consensus</div></div>
  <div class=stat><div class=n>{FINAL.get('evidence_ratio')}</div><div class=l>evidence ratio</div></div>
  <div class=stat><div class=n>{e(best)}</div><div class=l>leader model</div></div>
  <div class=stat><div class=n>{len(rounds)}</div><div class=l>rounds</div></div>
</div></header>

<section><h2>Consensus &mdash; monotonic, never decreases</h2><div class=rounds>{rps}</div></section>
<section><h2>Ranked plan</h2><ol>{plan}</ol></section>
<section><h2>Solutions to each objection</h2>{fixes or '<p class=note>n/a</p>'}</section>
<section><h2>Evidence ledger &mdash; supported {c.get('supported',0)} &middot; refuted {c.get('refuted',0)} &middot; uncertain {c.get('uncertain',0)}</h2>{verds}</section>
<section><h2>Builds on existing tools</h2><div class=tags>{tools or '<span>n/a</span>'}</div></section>
<section><h2>Model leaderboard</h2>{lbrows}</section>
<section><h2>Minority report &mdash; {e(mr.get('seat',''))}</h2><div class=mr>{e(mr.get('reason',''))}</div></section>

<section><h2>Does the council beat one model?</h2>
<p class=note>Same question, single model vs. the full council, scored by an independent judge on a rubric.</p>
<div class=vs>
  <div class=col><div class=sc>{st}</div><div class='bar' style='height:{int(st/mx*120)}px'></div><div class=l>single model</div></div>
  <div class=col><div class=sc style='color:var(--amber)'>{ct}</div><div class='bar win' style='height:{int(ct/mx*120)}px'></div><div class=l>full council</div></div>
</div>
<p class=note style='margin-top:12px'>{e(EVAL.get('rationale',''))[:400]}</p></section>

<footer>
<p>This is a static snapshot. The live interactive app (watch a debate unfold, chat with the Leader)
runs locally:</p>
<div class=run>git clone https://github.com/praveen-sri-krishna/ai-council
cd ai-council &amp;&amp; uv sync
cp .env.example .env   # add free API keys
uv run python gui.py   # http://127.0.0.1:7860</div>
<p style='margin-top:16px'>Source &amp; full debate JSON:
<a href="https://github.com/praveen-sri-krishna/ai-council">github.com/praveen-sri-krishna/ai-council</a></p>
</footer></div></body></html>"""


if __name__ == "__main__":
    Path("docs").mkdir(exist_ok=True)
    Path("docs/index.html").write_text(render(), encoding="utf-8")
    print("wrote docs/index.html")
