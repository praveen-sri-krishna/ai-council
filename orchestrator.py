"""The debate loop: research/prior-art -> propose -> critique -> web-ground ->
revise -> synthesize -> vote (monotonic, to threshold).

Phase 3 adds: keyless web grounding with claim verification, a GitHub prior-art
check so the panel builds on existing work, a consensus score that never
decreases across rounds, and an out-of-the-box (lateral) pass when it stalls.
"""
import json
import re
import sys
from statistics import mean

from memory import MemoryPalace, retrieve_cases, write_case
from research import gather_evidence, github_prior_art, search_web
from seats import call_seat, extract_json

# Windows console is cp1252; model output is full Unicode. Don't let a stray
# non-breaking hyphen crash a debate.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAX_CLAIMS = 5  # cap verified claims per debate to bound search quota

# --- Event stream: phases emit structured events. A GUI registers a hook via
# set_emit(); the CLI default pretty-prints. One debate per process. ---
_emit_hook = None


def set_emit(fn) -> None:
    global _emit_hook
    _emit_hook = fn


def emit(event: dict) -> None:
    if _emit_hook:
        try:
            _emit_hook(event)
            return
        except Exception:
            pass
    _console(event)


def _console(e: dict) -> None:
    t = e.get("type")
    if t == "start":
        print(f"\n=== DEBATE: {e['idea']!r}")
        print(f"seats: {', '.join(e['seats'])} | chair={e['chair']} "
              f"researcher={e['researcher']} grounded={e['grounded']}\n")
    elif t == "phase":
        print(e["name"])
    elif t == "memory":
        print(f"  [memory] {e['msg']}")
    elif t == "research":
        print(f"  [research] {e['count']} existing tools found; top: {e['top']}")
    elif t == "propose":
        print(f"  [propose] {e['seat']}: {e['proposal'][:80]}")
    elif t == "critique":
        print(f"  [critique] {e['seat']} scored {e['n']} peers")
    elif t == "webground":
        print(f"  [web-ground] {e['verdict']:9} | {e['claim'][:70]}")
    elif t == "webground_summary":
        print(f"  [web-ground] {e['counts']} | evidence_ratio (supported/total) = {e['ratio']}")
    elif t == "revise":
        print(f"  [revise] {e['n']} seats revised")
    elif t == "lateral":
        print(f"  [lateral] injected {e['key']}: {e['proposal'][:70]}")
    elif t == "round":
        print(f"  round {e['n']}: this={e['this']:.3f} best(reported)={e['best']:.3f} "
              f"(threshold {e['threshold']})")
    elif t == "final":
        print(f"  [memory] case saved | best model: {e['final'].get('best_model')}")


def ask_json(seat, system: str, user: str, defaults: dict, max_tokens: int | None = None):
    """Resilient: call_seat already retries transient errors; if a seat still hard-
    fails (bad key, down provider), return (None, reason) so the debate degrades
    gracefully instead of crashing."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        text = call_seat(seat, msgs, defaults, max_tokens=max_tokens)
    except Exception as e:
        return None, f"(seat {seat.id} unavailable: {type(e).__name__})"
    data = extract_json(text)
    if data is None:
        msgs += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "Output ONLY valid JSON. No prose, no markdown fences."},
        ]
        try:
            text = call_seat(seat, msgs, defaults, max_tokens=max_tokens)
        except Exception:
            return None, text
        data = extract_json(text)
    return data, text


def pick_chair(seats: list):
    for role in ("chair", "generalist"):
        for s in seats:
            if s.role == role:
                return s
    return seats[0]


def pick_researcher(seats: list):
    for role in ("chair", "researcher", "generalist"):
        for s in seats:
            if s.role == role:
                return s
    return seats[0]


# --- Phase 0: research + prior art ---------------------------------------

def phase_research(idea: str, researcher, mp: MemoryPalace, cfg) -> None:
    provider = getattr(cfg, "search_provider", "duckduckgo")
    system = (
        "You turn a problem into search inputs. Respond ONLY as JSON: "
        '{"github_query": "<keywords to find existing tools/repos>", '
        '"context_queries": ["<web query>", "<web query>"]}'
    )
    data, _ = ask_json(researcher, system, f"Problem: {idea}", cfg.defaults)
    data = data or {"github_query": idea, "context_queries": [idea]}

    prior_art = github_prior_art(data.get("github_query", idea), max_results=6)
    if not prior_art:  # a long sentence query returns nothing; fall back to keywords
        kws = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9+#-]{3,}", idea)
               if w.lower() not in ("design", "build", "create", "with", "that", "this",
                                    "without", "across", "their")][:5]
        prior_art = github_prior_art(" ".join(kws), max_results=6)
    context = []
    for q in (data.get("context_queries") or [])[:2]:
        context.append({"query": q, "results": search_web(q, max_results=4, provider=provider)})

    mp.research = {"queries": data, "prior_art": prior_art, "context": context,
                   "verdicts": [], "evidence_ratio": None}
    mp.log("researcher", f"prior_art={len(prior_art)} repos, context_queries={len(context)}")
    emit({"type": "research", "count": len(prior_art),
          "top": prior_art[0]["repo"] if prior_art else "none", "prior_art": prior_art})


def _prior_art_brief(mp: MemoryPalace, n: int = 5) -> str:
    pa = mp.research.get("prior_art", [])[:n]
    if not pa:
        return "(no prior-art results)"
    return "\n".join(f"- {p['repo']} ({p['stars']} stars): {p['desc']}" for p in pa)


# --- Phase 1: propose -----------------------------------------------------

def phase_propose(idea: str, seats: list, mp: MemoryPalace, defaults: dict,
                  use_research: bool) -> None:
    extra = ""
    cases = mp.research.get("past_cases") or []
    if cases:
        extra += "\n\nRelevant past council debates (learn from these outcomes):\n" + "\n".join(cases)[:2000]
    if use_research:
        extra += ("\n\nExisting tools/prior art (build ON these where sensible, "
                  "don't reinvent):\n" + _prior_art_brief(mp))
    for s in seats:
        system = (
            f"You are a {s.role} on an expert council. {s.personality} "
            "Give your independent best plan. Where a proven existing tool fits, adopt or "
            "build on it rather than reinventing. Do not hedge. "
            'Respond ONLY as JSON: {"proposal": "<concise plan>", '
            '"reasoning": "<why>", "builds_on": ["<existing tool/repo if any>"], '
            '"claims_to_verify": ["<checkable factual claim>", ...]}'
        )
        data, raw = ask_json(s, system, f"Problem: {idea}{extra}", defaults)
        if not data:
            data = {"proposal": raw[:800], "reasoning": "(unparsed)",
                    "builds_on": [], "claims_to_verify": []}
        mp.add_proposal(s.id, s.role, data)
        mp.log(s.id, f"proposed: {data.get('proposal', '')[:160]}")
        emit({"type": "propose", "seat": s.id, "role": s.role,
              "proposal": data.get("proposal", ""), "builds_on": data.get("builds_on", [])})


# --- Phase 2: critique ----------------------------------------------------

def phase_critique(seats: list, mp: MemoryPalace, defaults: dict) -> None:
    board = {sid: p["proposal"] for sid, p in mp.proposals.items()}
    for s in seats:
        others = {sid: txt for sid, txt in board.items() if sid != s.id}
        if not others:
            continue
        system = (
            f"You are a {s.role}. {s.personality} Critique each peer proposal honestly. "
            "For EACH id return: score (0.0-1.0), strengths, weaknesses, "
            "hidden_assumptions, failure_modes. ONLY JSON: "
            '{"<id>": {"score": 0.0, "strengths": "", "weaknesses": "", '
            '"hidden_assumptions": "", "failure_modes": ""}, ...}'
        )
        data, raw = ask_json(s, system, "Proposals:\n" + json.dumps(others, indent=2), defaults)
        mp.add_critique(s.id, data or {"_raw": raw[:600]})
        emit({"type": "critique", "seat": s.id, "n": len(data) if data else 0})


# --- Phase 3: web-ground (verify flagged claims) --------------------------

def phase_webground(researcher, mp: MemoryPalace, cfg) -> None:
    provider = getattr(cfg, "search_provider", "duckduckgo")
    claims = []
    for p in mp.proposals.values():
        for c in p.get("claims_to_verify", []) or []:
            if c and c not in claims:
                claims.append(c)
    claims = claims[:MAX_CLAIMS]
    if not claims:
        emit({"type": "webground_summary", "counts": {}, "ratio": None})
        return

    verdicts = []
    for c in claims:
        ev = gather_evidence(c, max_results=4, provider=provider)
        system = (
            "You are a fact-checker. Given supporting and counter evidence, judge the claim. "
            "Be skeptical: if evidence is thin or mixed, say uncertain. ONLY JSON: "
            '{"verdict": "supported|refuted|uncertain", "confidence": 0.0, "note": "", '
            '"source": "<url or empty>"}'
        )
        user = json.dumps({"claim": c,
                           "supporting": [e["snippet"][:200] for e in ev["supporting"]],
                           "counter": [e["snippet"][:200] for e in ev["counter"]]}, indent=2)
        data, _ = ask_json(researcher, system, user, cfg.defaults)
        data = data or {"verdict": "uncertain", "confidence": 0.0, "note": "(no parse)", "source": ""}
        data["claim"] = c
        verdicts.append(data)
        emit({"type": "webground", "verdict": data["verdict"], "claim": c,
              "note": data.get("note", "")})

    n = len(verdicts)
    counts = {k: sum(1 for v in verdicts if v["verdict"] == k)
              for k in ("supported", "refuted", "uncertain")}
    # "majority evidence-based" = supported share of ALL claims (uncertain counts against).
    ratio = round(counts["supported"] / n, 3) if n else None
    mp.research["verdicts"] = verdicts
    mp.research["evidence_counts"] = counts
    mp.research["evidence_ratio"] = ratio
    emit({"type": "webground_summary", "counts": counts, "ratio": ratio})


def _evidence_brief(mp: MemoryPalace) -> str:
    v = mp.research.get("verdicts", [])
    if not v:
        return "(no claims verified)"
    return "\n".join(f"- [{x['verdict']}] {x['claim'][:90]} ({x.get('note','')[:60]})" for x in v)


# --- Phase 4: revise ------------------------------------------------------

def phase_revise(seats: list, mp: MemoryPalace, defaults: dict, grounded: bool) -> None:
    evidence = _evidence_brief(mp) if grounded else "(grounding disabled)"
    for s in seats:
        mine = mp.proposals.get(s.id)
        if not mine:
            continue
        objections = {cid: c.get(s.id) for cid, c in mp.critiques.items()
                      if isinstance(c, dict) and c.get(s.id)}
        system = (
            f"You are a {s.role}. {s.personality} Revise YOUR plan to address the objections "
            "and the evidence. Drop or fix any claim the evidence refuted; prefer "
            "evidence-supported choices. ONLY JSON: "
            '{"proposal": "<revised plan>", "reasoning": "<what changed and why>"}'
        )
        user = json.dumps({"your_plan": mine.get("proposal", ""),
                           "objections": objections, "evidence": evidence}, indent=2)[:9000]
        data, _ = ask_json(s, system, user, defaults)
        if data and data.get("proposal"):
            mp.proposals[s.id]["proposal"] = data["proposal"]
            mp.proposals[s.id]["reasoning"] = data.get("reasoning", mine.get("reasoning", ""))
    emit({"type": "revise", "n": len(seats)})


# --- Phase 5: synthesize (resolve objections, then improve from best) ------

def _objections(mp: MemoryPalace, best: dict | None) -> list[str]:
    """The concrete faults the plan must fix: panel vote reasons + the weaknesses
    and failure modes raised in critique."""
    out = []
    if best:
        out += [v.get("reason", "") for v in best["votes"].values() if v.get("reason")]
    for targets in mp.critiques.values():
        if isinstance(targets, dict):
            for c in targets.values():
                if isinstance(c, dict):
                    for fld in ("weaknesses", "failure_modes"):
                        if c.get(fld):
                            out.append(c[fld])
    seen, uniq = set(), []
    for o in out:
        # models format these fields as str OR list -> coerce to a clean string
        if isinstance(o, list):
            o = "; ".join(str(x) for x in o)
        o = str(o).strip()
        k = o[:80]
        if o and k not in seen:
            seen.add(k)
            uniq.append(o)
    return uniq[:8]


def phase_synthesize(chair, mp: MemoryPalace, best: dict | None, defaults: dict) -> dict:
    system = (
        f"You are the chair. {chair.personality} Produce ONE ranked plan the panel will accept. "
        "PRIORITY: for each objection, devise a CONCRETE solution that fixes that specific fault "
        "and fold it into the plan -- do not merely restate or drop the contested part. Base "
        "claims on the verified evidence (drop refuted ones). Build on the listed existing tools "
        "where sensible. ONLY JSON: "
        '{"fixes": [{"objection": "<the fault>", "solution": "<concrete change that resolves it>"}], '
        '"ranked_plan": ["step", ...], "builds_on": ["existing tool", ...], '
        '"dissent": ["disagreement that genuinely could NOT be resolved", ...], '
        '"confidence": 0.0, "rationale": ""}'
    )
    objections = _objections(mp, best)
    payload = {
        "objections_to_resolve": objections,
        "proposals": {k: v["proposal"] for k, v in mp.proposals.items()},
        "evidence": _evidence_brief(mp),
        "prior_art": _prior_art_brief(mp),
    }
    if best is not None:
        payload["current_best_plan"] = best["proposal"].get("ranked_plan", [])
        payload["instruction"] = ("Resolve every objection in objections_to_resolve with a "
                                  "concrete fix, then output the improved plan. Keep what works.")
    data, raw = ask_json(chair, system, json.dumps(payload, indent=2)[:12000], defaults)
    return data or {"fixes": [], "ranked_plan": [raw[:800]], "builds_on": [], "dissent": [],
                    "confidence": 0.0, "rationale": "(unparsed)"}


# --- Phase 6: vote --------------------------------------------------------

def synth_with_failover(primary, mp: MemoryPalace, best: dict | None,
                        defaults: dict, seats: list) -> dict:
    """Synthesis is the one single-seat step -> if the chair can't produce a usable
    plan (down/rate-limited), fail over to the next available seat."""
    order = [primary] + [s for s in seats if s.id != primary.id]
    synth = None
    for s in order:
        synth = phase_synthesize(s, mp, best, defaults)
        if synth.get("ranked_plan") and synth.get("rationale") != "(unparsed)":
            if s.id != primary.id:
                emit({"type": "phase", "name": f"  (chair failover -> {s.id})"})
            return synth
    return synth or {"fixes": [], "ranked_plan": [], "builds_on": [], "dissent": [],
                     "confidence": 0.0, "rationale": "(all seats failed)"}


def phase_vote(synthesis: dict, seats: list, defaults: dict) -> dict:
    votes = {}
    for s in seats:
        system = (
            f"You are a {s.role}. {s.personality} Score the plan 0.0-1.0 on quality, evidence, "
            'and whether it resolves your concerns. ONLY JSON: {"score": 0.0, "reason": ""}'
        )
        data, raw = ask_json(s, system, "Plan:\n" + json.dumps(synthesis, indent=2), defaults)
        score = float(data.get("score", 0.0)) if data else 0.0
        votes[s.id] = {"score": max(0.0, min(1.0, score)),
                       "reason": (data or {}).get("reason", raw[:200])}
    return votes


# --- Out-of-the-box (lateral) pass ---------------------------------------

def phase_lateral(idea: str, dissenter, mp: MemoryPalace, defaults: dict) -> None:
    system = (
        f"You are a lateral thinker. {dissenter.personality} The panel is stuck. Reframe the "
        "problem and propose a GENUINELY different approach the others overlooked -- a different "
        "architecture, an existing tool from the prior art, or an inverted assumption. "
        'ONLY JSON: {"proposal": "<unconventional plan>", "reasoning": "<the reframe>", '
        '"builds_on": ["existing tool if any"]}'
    )
    user = f"Problem: {idea}\n\nPrior art:\n{_prior_art_brief(mp)}"
    data, raw = ask_json(dissenter, system, user, defaults)
    if data and data.get("proposal"):
        key = f"lateral-{sum(1 for k in mp.proposals if k.startswith('lateral'))+1}"
        mp.add_proposal(key, "lateral", data)
        mp.log(key, f"lateral: {data['proposal'][:140]}")
        emit({"type": "lateral", "key": key, "proposal": data["proposal"],
              "builds_on": data.get("builds_on", [])})


def consensus_penalty(scores: list[float]) -> float:
    low = sum(1 for x in scores if x <= 0.55)
    high = sum(1 for x in scores if x >= 0.75)
    return 0.06 if (low >= 2 and high >= 1) else 0.0


def compute_leaderboard(mp: MemoryPalace) -> dict:
    """Which seat performed best: average peer-critique score its proposal received,
    blended with how it voted in the winning round. Drives the 'leader' pick."""
    received: dict[str, list[float]] = {}
    for critic, targets in mp.critiques.items():
        if not isinstance(targets, dict):
            continue
        for target, c in targets.items():
            if isinstance(c, dict) and "score" in c:
                try:
                    received.setdefault(target, []).append(float(c["score"]))
                except Exception:
                    pass
    board = {sid: round(mean(v), 3) for sid, v in received.items() if v}
    return dict(sorted(board.items(), key=lambda kv: kv[1], reverse=True))


# --- Driver ---------------------------------------------------------------

def run_debate(idea: str, seats: list, cfg, session_path: str | None = None) -> MemoryPalace:
    d = cfg.defaults
    mp = MemoryPalace(prompt=idea)
    chair = pick_chair(seats)
    researcher = pick_researcher(seats)
    dissenter = next((s for s in seats if s.role in ("dissenter", "red-teamer")), seats[-1])
    grounded = cfg.privacy_mode != "local_only"   # local_only = no search egress

    emit({"type": "start", "idea": idea, "seats": [s.id for s in seats],
          "chair": chair.id, "researcher": researcher.id, "grounded": grounded})

    # Memento is local SQLite -> case recall works in any privacy mode.
    mp.research["past_cases"] = retrieve_cases(idea)
    if mp.research["past_cases"]:
        emit({"type": "memory", "msg": f"recalled {len(mp.research['past_cases'])} past case(s)"})

    if grounded:
        emit({"type": "phase", "name": "RESEARCH"}); phase_research(idea, researcher, mp, cfg)
    emit({"type": "phase", "name": "PROPOSE"}); phase_propose(idea, seats, mp, d, use_research=grounded)
    emit({"type": "phase", "name": "CRITIQUE"}); phase_critique(seats, mp, d)
    if grounded:
        emit({"type": "phase", "name": "WEB-GROUND"}); phase_webground(researcher, mp, cfg)
    emit({"type": "phase", "name": "REVISE"}); phase_revise(seats, mp, d, grounded)

    emit({"type": "phase", "name": "SYNTHESIZE -> VOTE (monotonic)"})
    best = None
    stalled = False
    for it in range(cfg.max_iterations):
        if stalled and best and best["adjusted"] < cfg.consensus_threshold:
            emit({"type": "phase", "name": "  (stalled -> out-of-the-box pass)"})
            phase_lateral(idea, dissenter, mp, d)
            stalled = False
        synth = synth_with_failover(chair, mp, best, d, seats)
        votes = phase_vote(synth, seats, d)
        scores = [v["score"] for v in votes.values()]
        avg = mean(scores) if scores else 0.0
        adjusted = max(0.0, avg - consensus_penalty(scores))

        improved = best is None or adjusted > best["adjusted"]
        if improved:
            best = {"proposal": synth, "votes": votes, "avg": avg, "adjusted": adjusted}
            stalled = False
        else:
            stalled = True
        reported = best["adjusted"]   # never decreases
        mp.add_synthesis_round(synth, votes, avg, reported)
        emit({"type": "round", "n": it + 1, "this": adjusted, "best": reported,
              "threshold": cfg.consensus_threshold})
        if best["adjusted"] >= cfg.consensus_threshold:
            break

    dissent_id = min(best["votes"], key=lambda k: best["votes"][k]["score"])
    leaderboard = compute_leaderboard(mp)
    best_model = next(iter(leaderboard), chair.id)
    mp.final = {
        "ranked_plan": best["proposal"].get("ranked_plan", []),
        "fixes": best["proposal"].get("fixes", []),
        "builds_on": best["proposal"].get("builds_on", []),
        "dissent": best["proposal"].get("dissent", []),
        "confidence": round(best["adjusted"], 3),
        "evidence_ratio": mp.research.get("evidence_ratio"),
        "evidence_counts": mp.research.get("evidence_counts"),
        "verdicts": mp.research.get("verdicts", []),
        "leaderboard": leaderboard,
        "best_model": best_model,
        "minority_report": {"seat": dissent_id, **best["votes"][dissent_id]},
    }
    if session_path:
        mp.save(session_path)
    write_case(mp)
    emit({"type": "final", "final": mp.final})
    return mp


if __name__ == "__main__":
    from seats import available_seats, load_config

    cfg = load_config()
    avail = {s.id: s for s in available_seats(cfg)}
    # Phase 3 demo: fast hosted trio (open mode) to exercise grounding end-to-end.
    want = ["cerebras-fast", "groq-fast", "gemini-chair"]
    seats = [avail[i] for i in want if i in avail] or list(avail.values())[:3]

    idea = "Design a local-first personal note-taking app that syncs across devices without a central cloud server."
    mp = run_debate(idea, seats, cfg, session_path="sessions/phase3_demo.json")

    print("\n=== FINAL ===")
    print(json.dumps(mp.final, indent=2, ensure_ascii=False)[:2500])
    print("\nfull palace saved to sessions/phase3_demo.json")
