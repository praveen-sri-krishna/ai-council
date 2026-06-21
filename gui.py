"""AI Council GUI -- "Situation Room".

A live deliberation viewer: watch proposals, critiques, evidence verdicts and the
consensus climb in real time; read the final ranked plan, evidence ledger and
leaderboard; then talk 1:1 with the Leader (the best-performing model).

Design: industrial-utilitarian x editorial. Ink canvas, single signal-amber
accent, monospace operational trace, editorial display type. Anchor: the live
consensus meter + seat cards that light up as each model speaks.
"""
import datetime
import html
import json
import threading
from pathlib import Path

import gradio as gr

import orchestrator
from documents import generate_document, list_documents
from leader import leader_chat, leader_id, load_session
from seats import available_seats, load_config

SESSIONS = Path("sessions")
SESSIONS.mkdir(exist_ok=True)

VERDICT_COLOR = {"supported": "var(--green)", "refuted": "var(--red)", "uncertain": "var(--amber)"}

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700&family=JetBrains+Mono:wght@400;600&display=swap');
:root{--ink:#0c0f13;--panel:#141a21;--panel2:#1b232d;--amber:#ffb000;--amber2:#ff8a00;
--text:#e7ebf0;--muted:#8a94a3;--green:#3ecf8e;--red:#ff5d5d;--line:#283441;}
.gradio-container{background:var(--ink)!important;color:var(--text)!important;
font-family:'JetBrains Mono',monospace!important;max-width:1280px!important;}
#sr-head{border-bottom:1px solid var(--line);padding:14px 6px 18px;margin-bottom:8px;}
#sr-head h1{font-family:'Fraunces',serif;font-weight:700;font-size:30px;letter-spacing:-.5px;
margin:0;color:var(--text);}
#sr-head h1 .ac{color:var(--amber);}
#sr-head p{color:var(--muted);margin:4px 0 0;font-size:12.5px;letter-spacing:.3px;}
.sr-card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;}
.sr-label{font-size:11px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin:0 0 8px;}
/* consensus meter */
.meter-wrap{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;}
.meter-num{font-family:'Fraunces',serif;font-size:42px;font-weight:700;line-height:1;color:var(--amber);}
.meter-sub{color:var(--muted);font-size:12px;margin-top:2px;}
.meter-track{position:relative;height:14px;background:var(--panel2);border-radius:8px;margin:14px 0 6px;overflow:hidden;}
.meter-fill{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,var(--amber2),var(--amber));
border-radius:8px;transition:width .5s cubic-bezier(.2,.8,.2,1);}
.meter-thresh{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--text);opacity:.65;}
.meter-rounds{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;}
.r-dot{font-size:11px;color:var(--muted);background:var(--panel2);border:1px solid var(--line);
border-radius:20px;padding:2px 9px;}
.r-dot b{color:var(--amber);}
/* trace feed */
#trace{height:440px;overflow-y:auto;display:flex;flex-direction:column;gap:7px;padding-right:6px;}
.ev{border-left:2px solid var(--line);padding:7px 11px;background:var(--panel);border-radius:0 8px 8px 0;
animation:rise .35s ease;}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.ev .who{font-size:10.5px;text-transform:uppercase;letter-spacing:1.4px;color:var(--muted);}
.ev .txt{font-size:13px;margin-top:3px;color:var(--text);}
.ev.phase{border-left-color:var(--amber);background:transparent;padding:10px 11px 2px;}
.ev.phase .who{color:var(--amber);font-family:'Fraunces',serif;font-size:14px;letter-spacing:1px;text-transform:none;}
.ev.propose{border-left-color:#6aa3ff;}
.ev.lateral{border-left-color:var(--amber);background:rgba(255,176,0,.06);}
.ev.round{border-left-color:var(--green);}
.chip{display:inline-block;font-size:10px;padding:1px 7px;border-radius:20px;border:1px solid var(--line);
color:var(--muted);margin-left:6px;}
.vchip{display:inline-block;font-size:10px;font-weight:600;padding:1px 8px;border-radius:4px;color:#0c0f13;}
/* results */
.res h3{font-family:'Fraunces',serif;color:var(--amber);font-size:16px;margin:2px 0 10px;}
.answer{background:rgba(255,176,0,.08);border:1px solid var(--amber);border-radius:10px;padding:13px 15px;margin-bottom:14px;}
.answer .atxt{font-size:15px;line-height:1.55;margin-top:6px;color:var(--text);}
.verdict{background:var(--panel2);border:2px solid var(--muted);border-radius:10px;padding:12px 15px;margin-bottom:12px;}
.verdict .atxt{font-size:14px;line-height:1.5;margin-top:5px;color:var(--text);}
.res ol{padding-left:18px;margin:0;} .res li{margin:0 0 9px;font-size:13.5px;line-height:1.5;}
.res .fix{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:9px 11px;margin:0 0 8px;}
.res .fix .o{color:var(--red);font-size:12px;} .res .fix .s{color:var(--green);font-size:12.5px;margin-top:3px;}
.lb-row{display:flex;align-items:center;gap:10px;margin:6px 0;flex-wrap:wrap;}
.lb-bar{height:10px;max-width:55%;background:linear-gradient(90deg,var(--amber2),var(--amber));border-radius:6px;}
.lb-name{width:150px;font-size:12.5px;} .lb-name .crown{color:var(--amber);}
.tag{display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:20px;
padding:2px 10px;font-size:11.5px;margin:3px 4px 0 0;color:var(--text);}
/* --- mobile --- */
@media (max-width:760px){
  .gradio-container{padding:0 6px!important;}
  #sr-head h1{font-size:26px;}
  #trace{height:300px;}
  .lb-name{width:92px;font-size:11.5px;}
  .lb-bar{max-width:40vw;}
  .stat .n{font-size:24px;}
  .meter-num{font-size:34px;}
  .res li{font-size:13px;}
}
"""


# ---------- renderers ----------

def _esc(s) -> str:
    return html.escape(str(s))


def render_meter(best: float | None, threshold: float, rounds: list[dict]) -> str:
    pct = int((best or 0) * 100)
    tpct = int(threshold * 100)
    converged = best is not None and best >= threshold
    state = "CONSENSUS REACHED" if converged else ("DELIBERATING" if rounds else "IDLE")
    dots = "".join(
        f"<span class='r-dot'>R{r['n']} <b>{r['best']:.2f}</b></span>" for r in rounds)
    return f"""
    <div class='meter-wrap'>
      <div style='display:flex;justify-content:space-between;align-items:flex-end'>
        <div><div class='meter-num'>{(best or 0):.3f}</div>
          <div class='meter-sub'>{state} &middot; threshold {threshold:.2f}</div></div>
        <div style='text-align:right' class='meter-sub'>consensus<br>confidence</div>
      </div>
      <div class='meter-track'>
        <div class='meter-fill' style='width:{pct}%'></div>
        <div class='meter-thresh' style='left:{tpct}%'></div>
      </div>
      <div class='meter-rounds'>{dots or "<span class='meter-sub'>awaiting first round…</span>"}</div>
    </div>"""


def render_trace(events: list[dict]) -> str:
    rows = []
    for e in events:
        t = e.get("type")
        if t == "start":
            rows.append(f"<div class='ev phase'><div class='who'>SESSION OPEN</div>"
                        f"<div class='txt'>{_esc(e['idea'])}<br>"
                        f"<span class='chip'>{len(e['seats'])} seats</span>"
                        f"<span class='chip'>chair: {_esc(e['chair'])}</span>"
                        f"<span class='chip'>grounded: {e['grounded']}</span></div></div>")
        elif t == "phase":
            rows.append(f"<div class='ev phase'><div class='who'>{_esc(e['name'])}</div></div>")
        elif t == "memory":
            rows.append(f"<div class='ev'><div class='who'>memory</div>"
                        f"<div class='txt'>{_esc(e['msg'])}</div></div>")
        elif t == "intent":
            rows.append(f"<div class='ev'><div class='who'>intent</div>"
                        f"<div class='txt'><span class='chip'>{_esc(e.get('mode'))}</span>"
                        f"{_esc(e.get('restate', ''))}</div></div>")
        elif t == "research":
            rows.append(f"<div class='ev'><div class='who'>prior art</div>"
                        f"<div class='txt'>{e['count']} existing tools &middot; top: "
                        f"<b>{_esc(e['top'])}</b></div></div>")
        elif t == "propose":
            bo = "".join(f"<span class='chip'>{_esc(x)}</span>" for x in (e.get("builds_on") or [])[:3])
            rows.append(f"<div class='ev propose'><div class='who'>{_esc(e['seat'])} "
                        f"&middot; {_esc(e['role'])}</div>"
                        f"<div class='txt'>{_esc(e['proposal'][:280])}{bo}</div></div>")
        elif t == "critique":
            rows.append(f"<div class='ev'><div class='who'>{_esc(e['seat'])}</div>"
                        f"<div class='txt'>critiqued {e['n']} peers</div></div>")
        elif t == "webground":
            col = VERDICT_COLOR.get(e["verdict"], "var(--muted)")
            rows.append(f"<div class='ev'><div class='who'>fact-check</div>"
                        f"<div class='txt'><span class='vchip' style='background:{col}'>"
                        f"{_esc(e['verdict'])}</span> {_esc(e['claim'][:140])}</div></div>")
        elif t == "webground_summary":
            c = e.get("counts") or {}
            rows.append(f"<div class='ev'><div class='who'>evidence</div>"
                        f"<div class='txt'>supported {c.get('supported',0)} &middot; "
                        f"refuted {c.get('refuted',0)} &middot; uncertain {c.get('uncertain',0)} "
                        f"&middot; <b>ratio {e.get('ratio')}</b></div></div>")
        elif t == "revise":
            rows.append(f"<div class='ev'><div class='who'>revise</div>"
                        f"<div class='txt'>{e['n']} seats revised their plans</div></div>")
        elif t == "lateral":
            rows.append(f"<div class='ev lateral'><div class='who'>out-of-the-box</div>"
                        f"<div class='txt'>{_esc(e['proposal'][:240])}</div></div>")
        elif t == "round":
            arrow = "reached threshold" if e["best"] >= e["threshold"] else f"best {e['best']:.3f}"
            rows.append(f"<div class='ev round'><div class='who'>round {e['n']}</div>"
                        f"<div class='txt'>this round {e['this']:.3f} &middot; {arrow}</div></div>")
        elif t == "error":
            rows.append(f"<div class='ev' style='border-left-color:var(--red)'>"
                        f"<div class='who' style='color:var(--red)'>error</div>"
                        f"<div class='txt'>{_esc(e['msg'])}</div></div>")
    # Newest first: gr.HTML can't run a scroll script, so the latest event stays
    # visible at the top of the fixed-height feed.
    feed = "".join(reversed(rows)) or "<div class='meter-sub'>Run a deliberation to watch it unfold.</div>"
    return f"<div id='trace'>{feed}</div>"


def render_results(final: dict) -> tuple[str, str, str]:
    if not final:
        empty = "<div class='res'><p class='meter-sub'>No result yet.</p></div>"
        return empty, empty, empty
    mode = final.get("mode", "")
    body = final.get("key_points") or final.get("ranked_plan", [])
    body_heading = {"brainstorm": "Ideas", "decision": "Why &amp; trade-offs",
                    "analysis": "The reasoning", "build": "Plan",
                    "comparison": "Comparison", "factual": "Details"}.get(mode, "Key points")
    direct = final.get("direct_answer", "")
    points = "".join(f"<li>{_esc(p)}</li>" for p in body)
    fixes = "".join(f"<div class='fix'><div class='o'>&#9888; {_esc(f.get('objection',''))}</div>"
                    f"<div class='s'>&#10003; {_esc(f.get('solution',''))}</div></div>"
                    for f in final.get("fixes", []))
    tools = "".join(f"<span class='tag'>{_esc(t)}</span>" for t in final.get("builds_on", []))
    diffs = "".join(f"<li>{_esc(d)}</li>" for d in final.get("differentiators", []))
    comps = "".join(
        f"<div class='fix'><div class='s'>{_esc(c.get('name',''))}</div>"
        f"<div class='note'>{_esc(c.get('what_they_do',''))}</div>"
        f"<div class='o'>gap: {_esc(c.get('gap',''))}</div></div>"
        for c in final.get("competitors", []) if isinstance(c, dict))
    v = final.get("verdict") or {}
    call = str(v.get("call", "")).lower()
    vcol = {"pursue": "var(--green)", "pursue_with_changes": "var(--amber)",
            "pivot": "var(--amber)", "drop": "var(--red)"}.get(call, "var(--muted)")
    vlabel = {"pursue": "PURSUE", "pursue_with_changes": "PURSUE — WITH CHANGES",
              "pivot": "PIVOT", "drop": "DON'T BUILD THIS"}.get(call, call.upper())
    verdict_html = (f"<div class='verdict' style='border-color:{vcol}'>"
                    f"<div class='who' style='color:{vcol}'>REALITY CHECK &middot; {_esc(vlabel)}</div>"
                    f"<div class='atxt'>{_esc(v.get('odds',''))} {_esc(v.get('why',''))}</div></div>"
                    if call else "")
    lead = (f"<div class='answer'><div class='who' style='color:var(--amber)'>"
            f"ANSWER{(' &middot; ' + _esc(mode)) if mode else ''} &middot; confidence "
            f"{final.get('confidence')}</div><div class='atxt'>{_esc(direct)}</div></div>"
            if direct else "")
    plan_html = (f"<div class='res'>{verdict_html}{lead}"
                 + (f"<h3 style='margin-top:14px'>{body_heading}</h3><ol>{points}</ol>" if points else "")
                 + (f"<h3 style='margin-top:14px'>Why this beats what exists</h3><ol>{diffs}</ol>" if diffs else "")
                 + (f"<h3 style='margin-top:14px'>Competitors &amp; their gaps</h3>{comps}" if comps else "")
                 + (f"<h3 style='margin-top:14px'>Solutions to objections</h3>{fixes}" if fixes else "")
                 + (f"<h3 style='margin-top:14px'>Builds on</h3>{tools}" if tools else "") + "</div>")

    verds = "".join(
        f"<div class='ev'><div class='txt'><span class='vchip' style='background:"
        f"{VERDICT_COLOR.get(v.get('verdict'),'var(--muted)')}'>{_esc(v.get('verdict'))}</span> "
        f"{_esc(v.get('claim',''))}<br><span class='meter-sub'>{_esc(v.get('note',''))}</span></div></div>"
        for v in final.get("verdicts", []))
    c = final.get("evidence_counts") or {}
    ev_html = (f"<div class='res'><h3>Evidence ledger &middot; ratio {final.get('evidence_ratio')}</h3>"
               f"<p class='meter-sub'>supported {c.get('supported',0)} &middot; refuted "
               f"{c.get('refuted',0)} &middot; uncertain {c.get('uncertain',0)}</p>{verds}</div>")

    lb = final.get("leaderboard") or {}
    best = final.get("best_model")
    rows = ""
    for sid, score in lb.items():
        crown = " &#9818;" if sid == best else ""
        rows += (f"<div class='lb-row'><div class='lb-name'>{'<span class=crown>' if sid==best else ''}"
                 f"{_esc(sid)}{crown}{'</span>' if sid==best else ''}</div>"
                 f"<div class='lb-bar' style='width:{int(score*220)}px'></div>"
                 f"<div class='meter-sub'>{score}</div></div>")
    mr = final.get("minority_report") or {}
    lb_html = (f"<div class='res'><h3>Leaderboard &middot; leader: {_esc(best)}</h3>{rows}"
               f"<h3 style='margin-top:16px'>Minority report ({_esc(mr.get('seat',''))})</h3>"
               f"<div class='ev' style='border-left-color:var(--red)'><div class='txt'>"
               f"{_esc(mr.get('reason',''))}</div></div>"
               + ("".join(f"<div class='ev'><div class='txt'>&middot; {_esc(d)}</div></div>"
                          for d in final.get('dissent', []))) + "</div>")
    return plan_html, ev_html, lb_html


# ---------- background runner (decoupled from the connection) ----------
# The debate runs in a server-side thread that writes progress to a live file.
# Viewing is a separate, short poll -> robust to mobile app-switching / tunnel drops.
LIVE = SESSIONS / "_live.json"


def _write_live(state: dict) -> None:
    tmp = SESSIONS / "_live.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(LIVE)


def _read_live() -> dict:
    try:
        return json.loads(LIVE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def start_debate(idea: str, privacy: str, selected: list[str]):
    """Fire-and-forget: start the debate server-side and return immediately. It runs
    independently of this connection, so switching apps / losing signal is fine."""
    cfg = load_config()
    cfg.privacy_mode = privacy
    avail = {s.id: s for s in available_seats(cfg)}
    seats = [avail[i] for i in (selected or []) if i in avail]
    if len(seats) < 2 and len(avail) >= 2:
        seats = list(avail.values())

    if not idea.strip():
        return (render_trace([{"type": "error", "msg": "Enter an idea or problem to deliberate."}]),
                render_meter(None, cfg.consensus_threshold, []), *render_results({}))
    if len(seats) < 2:
        why = ("No local seats available — start Ollama with the models in config.yaml."
               if privacy == "local_only" else
               "No seats available — add API keys to .env (open mode) or start Ollama.")
        return (render_trace([{"type": "error", "msg": why}]),
                render_meter(None, cfg.consensus_threshold, []), *render_results({}))

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = str(SESSIONS / f"session_{stamp}.json")
    state = {"status": "running", "idea": idea, "events": [], "rounds": [],
             "best": None, "final": {}, "path": path}
    _write_live(state)

    def worker():
        def emit(ev: dict):
            if ev.get("type") == "round":
                state["rounds"].append({"n": ev["n"], "best": ev["best"]})
                state["best"] = ev["best"]
            elif ev.get("type") == "final":
                state["final"] = ev["final"]
            state["events"].append(ev)
            try:
                _write_live(state)
            except Exception:
                pass
        orchestrator.set_emit(emit)
        try:
            orchestrator.run_debate(idea, seats, cfg, session_path=path)
            state["status"] = "done"
        except Exception as ex:
            state["events"].append({"type": "error", "msg": f"{type(ex).__name__}: {ex}"})
            state["status"] = "error"
        _write_live(state)

    threading.Thread(target=worker, daemon=True).start()
    intro = [{"type": "phase", "name": "COUNCIL CONVENED — running in the background (~2-4 min)"},
             {"type": "memory", "msg": "Switch apps freely. The live view auto-updates; or tap "
              "'Refresh' anytime to see progress / the final answer."}]
    return (render_trace(intro), render_meter(None, cfg.consensus_threshold, []), *render_results({}))


def refresh_view():
    """Show current progress or the final result from the live file (or, if none,
    the latest saved session). A short request -> works fine on mobile/tunnel."""
    cfg = load_config()
    st = _read_live()
    if not st:
        sess = list_sessions()
        if not sess:
            return (render_trace([{"type": "error", "msg": "No debates yet — enter an idea and "
                                   "tap 'Convene the council'."}]),
                    render_meter(None, cfg.consensus_threshold, []), *render_results({}))
        s = load_session(sess[0])
        final = s.get("final", {})
        rounds = [{"n": i + 1, "best": r["adjusted"]} for i, r in enumerate(s.get("synthesis", []))]
        best = rounds[-1]["best"] if rounds else final.get("confidence")
        return (render_trace([{"type": "phase", "name": "LATEST SAVED RESULT"}]),
                render_meter(best, cfg.consensus_threshold, rounds), *render_results(final))

    status = st.get("status", "running")
    label = {"running": "RUNNING…", "done": "DONE", "error": "ERROR"}.get(status, status.upper())
    events = list(st.get("events", [])) + [{"type": "phase", "name": f"STATUS: {label}"}]
    return (render_trace(events), render_meter(st.get("best"), cfg.consensus_threshold,
            st.get("rounds", [])), *render_results(st.get("final", {})))


# ---------- leader chat ----------

def _session_label(p: Path) -> str:
    """Human-readable dropdown label: idea + mode/verdict + date (not session_123.json)."""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        idea = (d.get("prompt") or "").strip()
        short = idea[:60] + ("…" if len(idea) > 60 else "")
        f = d.get("final", {})
        tag = " · ".join(x for x in [f.get("mode", ""),
                                     (f.get("verdict") or {}).get("call", "")] if x)
        ds = p.stem.replace("session_", "")
        when = f"{ds[4:6]}-{ds[6:8]} {ds[9:11]}:{ds[11:13]}" if len(ds) >= 13 else ds
        return f"{short or p.stem}" + (f"  [{tag}]" if tag else "") + f"  ({when})"
    except Exception:
        return p.name


def list_sessions() -> list[tuple[str, str]]:
    """(readable label, path) tuples for the dropdown, newest first."""
    paths = [p for p in SESSIONS.glob("*.json") if not p.name.startswith(("_", "."))]
    return [(_session_label(p), str(p)) for p in sorted(paths, reverse=True)]


def on_generate_doc(path: str):
    """Ask the leader to write a full presentable document for this debate."""
    if not path or not Path(path).exists():
        return "_Pick a session first._", gr.update()
    try:
        doc_path, _ = generate_document(path)
        return (f"Document saved: **{Path(doc_path).name}** — open the Library tab to read/download.",
                gr.update(choices=list_documents()))
    except Exception as e:
        return f"_Could not generate: {type(e).__name__}_", gr.update()


def on_pick_document(path: str):
    """Preview a saved document + expose it for download."""
    if not path or not Path(path).exists():
        return "_No document selected._", None
    return Path(path).read_text(encoding="utf-8"), path


def on_pick_session(path: str) -> str:
    if not path or not Path(path).exists():
        return "_No session selected._"
    s = load_session(path)
    lid = leader_id(s) or "(none)"
    return f"**Leader:** `{lid}` &nbsp;·&nbsp; debate: _{s.get('prompt','')[:90]}_"


def on_leader_msg(path: str, msg: str, history: list):
    history = history or []
    if not path or not Path(path).exists():
        history.append({"role": "assistant", "content": "Pick a saved session first."})
        return history, ""
    s = load_session(path)
    pairs = [(history[i]["content"], history[i + 1]["content"])
             for i in range(0, len(history) - 1, 2)]
    res = leader_chat(s, msg, pairs)
    notes = []
    if res.get("decision") == "consult_group":
        names = ", ".join(g["seat"] for g in res.get("group", []))
        notes.append(f"consulted the group ({names})")
    if res.get("researched"):
        notes.append(f"researched: {res['researched']}")
    tag = f"\n\n_— {' · '.join(notes)}_" if notes else ""
    history.append({"role": "user", "content": msg})
    history.append({"role": "assistant", "content": res.get("answer", "") + tag})
    return history, ""


# ---------- layout ----------

def build() -> gr.Blocks:
    cfg = load_config()
    seat_ids = [s.id for s in cfg.seats]
    default_sel = [i for i in ("cerebras-fast", "groq-fast", "gemini-chair") if i in seat_ids]

    with gr.Blocks(title="AI Council — Situation Room") as app:
        gr.HTML("<div id='sr-head'><h1>AI <span class='ac'>Council</span> "
                "&mdash; Situation Room</h1><p>A panel of models proposes, critiques, "
                "web-checks, and converges. Watch it think. Then brief the Leader.</p></div>")

        with gr.Tab("Deliberate"):
            with gr.Row():
                with gr.Column(scale=4):
                    gr.HTML("<p class='sr-label'>The problem</p>")
                    idea = gr.Textbox(placeholder="An idea or problem to put to the council…",
                                      lines=4, show_label=False)
                    privacy = gr.Radio(["open", "local_only"], value=cfg.privacy_mode,
                                       label="Privacy mode")
                    seats = gr.CheckboxGroup(seat_ids, value=default_sel, label="Seats")
                    run = gr.Button("Convene the council", variant="primary")
                    load = gr.Button("↻ Refresh / Load result", variant="secondary")
                    gr.HTML("<p class='note'>A debate runs on the server (~2-4 min) and keeps "
                            "going even if you switch apps or lose signal. The view auto-updates; "
                            "or tap <b>Refresh</b> anytime to see progress / the final answer.</p>")
                    meter = gr.HTML(render_meter(None, cfg.consensus_threshold, []))
                with gr.Column(scale=6):
                    gr.HTML("<p class='sr-label'>Live deliberation</p>")
                    trace = gr.HTML(render_trace([]))
            with gr.Row():
                with gr.Column():
                    plan = gr.HTML(render_results({})[0])
                with gr.Column():
                    evidence = gr.HTML(render_results({})[1])
                with gr.Column():
                    leaderboard = gr.HTML(render_results({})[2])
            outs = [trace, meter, plan, evidence, leaderboard]
            run.click(start_debate, [idea, privacy, seats], outs)
            load.click(refresh_view, None, outs)
            # Auto-update the view with short polls (robust over tunnel/mobile, no held stream).
            gr.Timer(5.0).tick(refresh_view, None, outs)

        with gr.Tab("Leader chat"):
            gr.HTML("<p class='sr-label'>Brief the leader 1:1 — they answer, "
                    "or take it back to the group</p>")
            with gr.Row():
                sess = gr.Dropdown(list_sessions(), label="Session", scale=4)
                refresh = gr.Button("↻", scale=1)
            who = gr.Markdown("_No session selected._")
            chat = gr.Chatbot(height=380)
            with gr.Row():
                msg = gr.Textbox(placeholder="Ask the leader about the plan, a dropped idea, "
                                 "a trade-off…", show_label=False, scale=5)
                send = gr.Button("Send", variant="primary", scale=1)
            with gr.Row():
                gen_doc = gr.Button("📄 Turn this into a document", variant="secondary")
                doc_status = gr.Markdown("")
            refresh.click(lambda: gr.update(choices=list_sessions()), None, sess)
            sess.change(on_pick_session, sess, who)
            send.click(on_leader_msg, [sess, msg, chat], [chat, msg])
            msg.submit(on_leader_msg, [sess, msg, chat], [chat, msg])

        with gr.Tab("Library"):
            gr.HTML("<p class='sr-label'>Your saved idea documents — preview & download</p>")
            with gr.Row():
                doc_sel = gr.Dropdown(list_documents(), label="Document", scale=4)
                doc_refresh = gr.Button("↻", scale=1)
            doc_file = gr.File(label="Download", interactive=False)
            doc_view = gr.Markdown("_Generate a document from the Leader chat tab, "
                                   "then pick it here._")
            doc_refresh.click(lambda: gr.update(choices=list_documents()), None, doc_sel)
            doc_sel.change(on_pick_document, doc_sel, [doc_view, doc_file])

        # Wired after the Library tab so the generate button can refresh its dropdown.
        gen_doc.click(on_generate_doc, sess, [doc_status, doc_sel])

    app.queue(default_concurrency_limit=4)  # required for reliable streaming generators
    return app


if __name__ == "__main__":
    # share=True needs Gradio's tunnel binary (may be network-blocked). The public
    # showcase lives on GitHub Pages (docs/, built by build_site.py); this serves locally.
    build().launch(server_name="127.0.0.1", server_port=7860, inbrowser=True,
                   css=CSS, theme=gr.themes.Base())
