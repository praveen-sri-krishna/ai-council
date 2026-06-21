"""The debate loop: research/prior-art -> propose -> critique -> web-ground ->
revise -> synthesize -> vote (monotonic, to threshold).

Phase 3 adds: keyless web grounding with claim verification, a GitHub prior-art
check so the panel builds on existing work, a consensus score that never
decreases across rounds, and an out-of-the-box (lateral) pass when it stalls.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from statistics import mean

from memory import MemoryPalace, retrieve_cases, write_case
from research import _keywords, gather_evidence, github_prior_art, search_web
from seats import call_seat, extract_json

# Windows console is cp1252; model output is full Unicode. Don't let a stray
# non-breaking hyphen crash a debate.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAX_CLAIMS = 3  # cap verified claims per debate (fewer web searches = faster)


def _pmap(items: list, fn, parallel: bool = True):
    """Map fn over items. Concurrent for hosted (I/O-bound API calls) -> big speedup;
    sequential when items include local seats (one model holds the GPU at a time).
    Preserves order."""
    items = list(items)
    if not items:
        return []
    if not parallel or len(items) == 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
        return list(ex.map(fn, items))


def _hosted_only(seats: list) -> bool:
    return not any(getattr(s, "local", False) for s in seats)

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
    elif t == "intent":
        print(f"  [intent] mode={e.get('mode')} | {e.get('restate', '')[:80]}")
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
    elif t == "escalation":
        print(f"  [escalation] brought in {e['seat']}: {e['proposal'][:60]}")
    elif t == "round":
        print(f"  round {e['n']}: this={e['this']:.3f} best(reported)={e['best']:.3f} "
              f"(threshold {e['threshold']})")
    elif t == "final":
        print(f"  [memory] case saved | best model: {e['final'].get('best_model')}")


def ask_json(seat, system: str, user: str, defaults: dict, max_tokens: int | None = None):
    """Resilient: call_seat already retries transient errors; if a seat still hard-
    fails (bad key, down provider), return (None, reason) so the debate degrades
    gracefully instead of crashing."""
    charter = defaults.get("charter", "")
    system = f"{charter}\n\n{system}" if charter else system
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


def _as_text(x) -> str:
    """Models sometimes return a field as a dict/list instead of a string. Coerce
    to text so downstream slicing/rendering never crashes."""
    if isinstance(x, str):
        return x
    if x is None:
        return ""
    return json.dumps(x, ensure_ascii=False)


def _as_list(x) -> list:
    if isinstance(x, list):
        return x
    return [x] if x else []


def pick_chair(seats: list):
    for role in ("chair", "generalist"):
        for s in seats:
            if s.role == role:
                return s
    return seats[0]


def pick_researcher(seats: list):
    # Prefer a fast researcher for light/frequent work; keep the strong chair for synthesis.
    for role in ("researcher", "generalist", "fast-backup", "fast-heavyweight", "chair"):
        for s in seats:
            if s.role == role:
                return s
    return seats[0]


# --- Phase 0a: classify intent (so the answer matches the question) -------

MODE_SHAPES = {
    "brainstorm": "a spread of distinct, divergent ideas/options — explore, don't converge to one",
    "decision": "a clear, decisive recommendation up front, then the few reasons and key trade-offs",
    "analysis": "explain the 'why' directly first, then the implications that follow from it",
    "build": "a concrete ordered plan of steps",
    "comparison": "a side-by-side of the options, then a clear verdict",
    "factual": "a direct, concise answer to exactly what was asked",
}


def phase_classify(idea: str, seat, mp: MemoryPalace, defaults: dict) -> dict:
    """Read the question's intent so the council answers in the RIGHT shape and
    leads with a direct answer instead of a generic data dump."""
    system = (
        "Classify the user's question so a panel answers it in the right shape. "
        "Pick mode from: brainstorm, decision, analysis, build, comparison, factual. "
        'ONLY JSON: {"mode": "<one>", "restate": "<one line: what they actually want>", '
        '"answer_shape": "<how the answer should look>"}'
    )
    data, _ = ask_json(seat, system, f"Question: {idea}", defaults, max_tokens=600)
    intent = data or {"mode": "analysis", "restate": idea, "answer_shape": MODE_SHAPES["analysis"]}
    if intent.get("mode") not in MODE_SHAPES:
        intent["mode"] = "analysis"
    intent.setdefault("answer_shape", MODE_SHAPES[intent["mode"]])
    mp.research["intent"] = intent
    emit({"type": "intent", "mode": intent["mode"], "restate": intent.get("restate", "")})
    return intent


def _intent(mp: MemoryPalace) -> dict:
    return mp.research.get("intent") or {"mode": "analysis", "restate": mp.prompt,
                                         "answer_shape": MODE_SHAPES["analysis"]}


# --- Phase 0: research + prior art ---------------------------------------

def phase_research(idea: str, researcher, mp: MemoryPalace, cfg) -> None:
    provider = getattr(cfg, "search_provider", "duckduckgo")
    system = (
        "Turn the problem into search inputs to map the CURRENT landscape. Respond ONLY as JSON: "
        '{"github_query": "<2-3 keywords for existing repos/tools>", '
        '"competitor_query": "<query to find direct products/competitors and alternatives>", '
        '"latest_query": "<query for the newest/trending tools in this field in 2026>", '
        '"context_queries": ["<web query>", "<web query>"]}'
    )
    data, _ = ask_json(researcher, system, f"Problem: {idea}", cfg.defaults)
    data = data or {}

    def kw_fallback() -> str:
        return " ".join(_keywords(idea)[:4])

    # All searches are independent + I/O-bound -> run them concurrently (big speedup).
    cq = (data.get("context_queries") or [])[:2]
    tasks = {
        "prior_art": lambda: github_prior_art(data.get("github_query") or kw_fallback(), 6)
                             or github_prior_art(kw_fallback(), 6),
        "latest_repos": lambda: github_prior_art(data.get("latest_query") or (kw_fallback() + " 2026"),
                                                 6, sort="updated"),
        "competitors": lambda: search_web(data.get("competitor_query") or f"{idea} competitors alternatives 2026",
                                          max_results=5, provider=provider),
        "latest_web": lambda: search_web(data.get("latest_query") or f"{idea} new tools 2026 trending",
                                         max_results=5, provider=provider),
    }
    keys = list(tasks)
    results = dict(zip(keys, _pmap(keys, lambda k: tasks[k]())))
    prior_art, latest_repos = results["prior_art"], results["latest_repos"]
    competitors, latest_web = results["competitors"], results["latest_web"]
    context = [{"query": q, "results": r} for q, r in
               zip(cq, _pmap(cq, lambda q: search_web(q, max_results=4, provider=provider)))] if cq else []

    mp.research.update({"queries": data, "prior_art": prior_art, "latest_repos": latest_repos,
                        "competitors_raw": competitors, "latest_web": latest_web, "context": context})
    mp.research.setdefault("verdicts", [])
    mp.research.setdefault("evidence_ratio", None)
    mp.log("researcher", f"prior_art={len(prior_art)} latest={len(latest_repos)} "
           f"competitors={len(competitors)}")
    emit({"type": "research", "count": len(prior_art) + len(latest_repos),
          "top": prior_art[0]["repo"] if prior_art else (latest_repos[0]["repo"] if latest_repos else "none"),
          "prior_art": prior_art})


def _prior_art_brief(mp: MemoryPalace, n: int = 5) -> str:
    pa = mp.research.get("prior_art", [])[:n]
    latest = mp.research.get("latest_repos", [])[:n]
    out = []
    if pa:
        out.append("Proven (by stars):\n" + "\n".join(
            f"- {p['repo']} ({p['stars']}*): {p['desc']}" for p in pa))
    if latest:
        out.append("Recently active / trending:\n" + "\n".join(
            f"- {p['repo']} (updated {p.get('updated','?')}): {p['desc']}" for p in latest))
    return "\n\n".join(out) or "(no prior-art results)"


def _market_brief(mp: MemoryPalace) -> str:
    comp = mp.research.get("competitors_raw", []) or []
    latest = mp.research.get("latest_web", []) or []
    out = []
    if comp:
        out.append("Competitors / products found:\n" + "\n".join(
            f"- {c.get('title','')}: {c.get('snippet','')[:160]}" for c in comp[:5]))
    if latest:
        out.append("Latest/trending in the field:\n" + "\n".join(
            f"- {c.get('title','')}: {c.get('snippet','')[:160]}" for c in latest[:5]))
    return "\n\n".join(out) or "(no market data)"


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
    intent = _intent(mp)

    def work(s):
        system = (
            f"You are a {s.role} on an expert council. {s.personality} "
            f"ANSWER THE ACTUAL QUESTION (\"{intent.get('restate', idea)}\") in this shape: "
            f"{intent.get('answer_shape')}. Lead with your direct answer, not preamble. "
            "Where a proven existing tool fits, adopt or build on it rather than reinventing. "
            'Respond ONLY as JSON: {"proposal": "<your direct answer in the right shape>", '
            '"reasoning": "<why>", "builds_on": ["<existing tool/repo if any>"], '
            '"claims_to_verify": ["<checkable factual claim>", ...]}'
        )
        data, raw = ask_json(s, system, f"Problem: {idea}{extra}", defaults)
        if not data:
            data = {"proposal": raw[:800], "reasoning": "(unparsed)",
                    "builds_on": [], "claims_to_verify": []}
        data["proposal"] = _as_text(data.get("proposal", ""))
        data["builds_on"] = _as_list(data.get("builds_on", []))
        data["claims_to_verify"] = _as_list(data.get("claims_to_verify", []))
        return s, data

    for s, data in _pmap(seats, work, parallel=_hosted_only(seats)):
        mp.add_proposal(s.id, s.role, data)
        mp.log(s.id, f"proposed: {data['proposal'][:160]}")
        emit({"type": "propose", "seat": s.id, "role": s.role,
              "proposal": data["proposal"], "builds_on": data["builds_on"]})


# --- Phase 2: critique ----------------------------------------------------

def phase_critique(seats: list, mp: MemoryPalace, defaults: dict) -> None:
    board = {sid: p["proposal"] for sid, p in mp.proposals.items()}

    def work(s):
        others = {sid: txt for sid, txt in board.items() if sid != s.id}
        if not others:
            return s, None, ""
        system = (
            f"You are a {s.role}. {s.personality} Critique each peer proposal honestly. "
            "For EACH id return: score (0.0-1.0), strengths, weaknesses, "
            "hidden_assumptions, failure_modes. ONLY JSON: "
            '{"<id>": {"score": 0.0, "strengths": "", "weaknesses": "", '
            '"hidden_assumptions": "", "failure_modes": ""}, ...}'
        )
        data, raw = ask_json(s, system, "Proposals:\n" + json.dumps(others, indent=2), defaults)
        return s, data, raw

    for s, data, raw in _pmap(seats, work, parallel=_hosted_only(seats)):
        if data is None and not raw:
            continue
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

    def verify(c):
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
        return data

    # Claims are independent + I/O-heavy (web search) -> verify concurrently.
    verdicts = _pmap(claims, verify, parallel=True)
    for data in verdicts:
        emit({"type": "webground", "verdict": data["verdict"], "claim": data["claim"],
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
            mp.proposals[s.id]["proposal"] = _as_text(data["proposal"])
            mp.proposals[s.id]["reasoning"] = _as_text(data.get("reasoning", mine.get("reasoning", "")))
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
    intent = _intent(mp)
    system = (
        f"You are the chair. {chair.personality} The user asked (mode={intent['mode']}): "
        f"\"{intent.get('restate', mp.prompt)}\". Answer it in this shape: {intent.get('answer_shape')}.\n"
        "FIRST give `direct_answer`: 2-4 sentences that directly and decisively answer the user's "
        "actual question in that shape (NOT a generic plan) -- this is what they read first. "
        "THEN `key_points`: the supporting detail, also in that shape (ideas if brainstorm, steps "
        "if build, the recommendation's reasons+trade-offs if decision, the explanation if analysis). "
        "For each objection, fold in a CONCRETE fix. Base claims on verified evidence (drop refuted). "
        "Use the provided competitor/market research: name the REAL current competitors & tools, and "
        "for each, state the concrete GAP this idea exploits. Then give clear `differentiators`: why "
        "this delivers what those competitors/tools cannot. Be specific -- no buzzwords.\n"
        "REALITY CHECK FIRST: give an honest `verdict` on whether this idea is even worth pursuing. "
        "If the space is saturated, the odds are slim, or it's a weak idea, say so -- call=pivot or "
        "drop -- and point to the better move. Do NOT default to 'pursue' to please the user. "
        "ONLY JSON: "
        '{"title": "<3-7 word label capturing the CORE IDEA/answer, not the user\'s opening words>", '
        '"verdict": {"call": "pursue|pursue_with_changes|pivot|drop", "odds": "<blunt honest read of the chances>", "why": "<the deciding reason>"}, '
        '"direct_answer": "<the answer, up front, in the right shape>", '
        '"key_points": ["<supporting point>", ...], '
        '"competitors": [{"name": "<real product/tool>", "what_they_do": "", "gap": "<what they miss>"}], '
        '"differentiators": ["<why ours wins where they cannot>", ...], '
        '"fixes": [{"objection": "<fault>", "solution": "<concrete fix>"}], '
        '"builds_on": ["existing tool", ...], '
        '"dissent": ["disagreement that could NOT be resolved", ...], '
        '"confidence": 0.0, "rationale": ""}'
    )
    objections = _objections(mp, best)
    payload = {
        "objections_to_resolve": objections,
        "proposals": {k: v["proposal"] for k, v in mp.proposals.items()},
        "evidence": _evidence_brief(mp),
        "prior_art": _prior_art_brief(mp),
        "market_and_competitors": _market_brief(mp),
    }
    if best is not None:
        payload["current_best_answer"] = {
            "direct_answer": best["proposal"].get("direct_answer", ""),
            "key_points": best["proposal"].get("key_points", best["proposal"].get("ranked_plan", [])),
        }
        payload["instruction"] = ("Resolve every objection with a concrete fix, then output the "
                                  "improved answer. Keep what works; keep leading with direct_answer.")
    data, raw = ask_json(chair, system, json.dumps(payload, indent=2)[:14000], defaults)
    return data or {"title": "", "verdict": {}, "direct_answer": raw[:800], "key_points": [], "competitors": [],
                    "differentiators": [], "fixes": [], "builds_on": [],
                    "dissent": [], "confidence": 0.0, "rationale": "(unparsed)"}


# --- Phase 6: vote --------------------------------------------------------

def synth_with_failover(primary, mp: MemoryPalace, best: dict | None,
                        defaults: dict, seats: list) -> dict:
    """Synthesis is the one single-seat step -> if the chair can't produce a usable
    plan (down/rate-limited), fail over to the next available seat."""
    order = [primary] + [s for s in seats if s.id != primary.id]
    synth = None
    for s in order:
        synth = phase_synthesize(s, mp, best, defaults)
        usable = synth.get("direct_answer") or synth.get("key_points") or synth.get("ranked_plan")
        if usable and synth.get("rationale") != "(unparsed)":
            if s.id != primary.id:
                emit({"type": "phase", "name": f"  (chair failover -> {s.id})"})
            return synth
    return synth or {"direct_answer": "", "key_points": [], "fixes": [], "builds_on": [],
                     "dissent": [], "confidence": 0.0, "rationale": "(all seats failed)"}


def phase_vote(synthesis: dict, seats: list, defaults: dict) -> dict:
    plan_str = "Plan:\n" + json.dumps(synthesis, indent=2)

    def work(s):
        system = (
            f"You are a {s.role}. {s.personality} Score the plan 0.0-1.0 on quality, evidence, "
            'and whether it resolves your concerns. ONLY JSON: {"score": 0.0, "reason": ""}'
        )
        data, raw = ask_json(s, system, plan_str, defaults)
        score = float(data.get("score", 0.0)) if data else 0.0
        return s.id, {"score": max(0.0, min(1.0, score)),
                      "reason": (data or {}).get("reason", raw[:200])}

    return {sid: v for sid, v in _pmap(seats, work, parallel=_hosted_only(seats))}


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
        data["proposal"] = _as_text(data["proposal"])
        data["builds_on"] = _as_list(data.get("builds_on", []))
        key = f"lateral-{sum(1 for k in mp.proposals if k.startswith('lateral'))+1}"
        mp.add_proposal(key, "lateral", data)
        mp.log(key, f"lateral: {data['proposal'][:140]}")
        emit({"type": "lateral", "key": key, "proposal": data["proposal"],
              "builds_on": data["builds_on"]})


def phase_escalate(idea: str, expert, mp: MemoryPalace, defaults: dict) -> None:
    """Reinforcement: when the council is unsure, bring in a stronger reasoner for a
    deeper take. Its proposal enters the pool so the next synthesis incorporates it."""
    system = (
        f"You are a senior expert brought in because the panel is stuck below the bar. "
        f"{expert.personality} Cut to the real crux: the decisive consideration, the sharpest "
        "reframe, or the strongest objection the others underweighted. Be concrete and decisive. "
        'ONLY JSON: {"proposal": "<your deeper take/answer>", "reasoning": "<the crux>", '
        '"builds_on": ["existing tool if any"]}'
    )
    user = (f"Problem: {idea}\n\nPrior art:\n{_prior_art_brief(mp)}\n\n"
            f"Current best answer:\n{json.dumps((mp.synthesis[-1]['proposal'] if mp.synthesis else {}), indent=2)[:3000]}")
    data, raw = ask_json(expert, system, user, defaults)
    if data and data.get("proposal"):
        data["proposal"] = _as_text(data["proposal"])
        data["builds_on"] = _as_list(data.get("builds_on", []))
        key = f"escalation-{sum(1 for k in mp.proposals if k.startswith('escalation'))+1}"
        mp.add_proposal(key, "escalation", data)
        mp.log(key, f"escalation({expert.id}): {data['proposal'][:140]}")
        emit({"type": "escalation", "seat": expert.id, "proposal": data["proposal"]})


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

def run_debate(idea: str, seats: list, cfg, session_path: str | None = None,
               escalation: list | None = None) -> MemoryPalace:
    d = cfg.defaults
    mp = MemoryPalace(prompt=idea)
    seats = list(seats)
    pool = list(escalation or [])   # stronger/extra models pulled in when the panel is unsure
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

    # Classify intent first so every phase answers the question in the right shape.
    emit({"type": "phase", "name": "CLASSIFY"}); phase_classify(idea, researcher, mp, d)

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
    escalations = 0
    lateral_done = False
    max_esc = getattr(cfg, "max_escalations", 1)
    for it in range(cfg.max_iterations):
        # Unsure (low confidence or a stalled round) -> escalate ONCE to a stronger
        # brain (which then leads synthesis), else stop. Keeps the common case fast.
        low_conf = best is not None and best["adjusted"] < cfg.consensus_threshold - 0.10
        if best is not None and best["adjusted"] < cfg.consensus_threshold and (stalled or low_conf):
            if pool and escalations < max_esc:
                expert = pool.pop(0)
                escalations += 1
                emit({"type": "phase", "name": f"  (unsure {best['adjusted']:.2f} -> escalating: + {expert.id})"})
                phase_escalate(idea, expert, mp, d)
                if expert.id not in {s.id for s in seats}:
                    seats.append(expert)
                chair = expert   # the brain now leads synthesis (deeper final answer)
                stalled = False
            elif not pool and not lateral_done:
                # No escalation pool (e.g. local_only) -> one out-of-the-box pass.
                emit({"type": "phase", "name": "  (stalled -> out-of-the-box pass)"})
                phase_lateral(idea, dissenter, mp, d)
                lateral_done = True
                stalled = False
            elif stalled:
                # No budget left and not improving -> stop early (don't grind).
                break
        synth = synth_with_failover(chair, mp, best, d, seats)
        votes = phase_vote(synth, seats, d)
        scores = [v["score"] for v in votes.values()]
        avg = mean(scores) if scores else 0.0
        adjusted = max(0.0, avg - consensus_penalty(scores))

        # Keep best on ANY gain (monotonic reporting), but a marginal gain (<0.02)
        # counts as stalled -> triggers escalation/early-stop instead of grinding.
        gained = best is None or adjusted > best["adjusted"]
        meaningful = best is None or adjusted > best["adjusted"] + 0.02
        if gained:
            best = {"proposal": synth, "votes": votes, "avg": avg, "adjusted": adjusted}
        stalled = not meaningful
        reported = best["adjusted"]   # never decreases
        mp.add_synthesis_round(synth, votes, avg, reported)
        emit({"type": "round", "n": it + 1, "this": adjusted, "best": reported,
              "threshold": cfg.consensus_threshold})
        if best["adjusted"] >= cfg.consensus_threshold:
            break

    dissent_id = min(best["votes"], key=lambda k: best["votes"][k]["score"])
    leaderboard = compute_leaderboard(mp)
    best_model = next(iter(leaderboard), chair.id)
    bp = best["proposal"]
    body = [_as_text(p) for p in _as_list(bp.get("key_points") or bp.get("ranked_plan", []))]
    mp.final = {
        "mode": _intent(mp).get("mode"),
        "question": _intent(mp).get("restate", mp.prompt),
        "title": _as_text(bp.get("title", "")).strip(),
        "verdict": bp.get("verdict", {}) if isinstance(bp.get("verdict"), dict) else {},
        "direct_answer": _as_text(bp.get("direct_answer", "")),
        "ranked_plan": body,            # back-compat key; holds the mode-shaped body
        "key_points": body,
        "competitors": bp.get("competitors", []),
        "differentiators": bp.get("differentiators", []),
        "fixes": bp.get("fixes", []),
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
