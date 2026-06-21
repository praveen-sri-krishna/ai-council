"""Smart model routing: pick the right roster for the task instead of one fixed set.

Strong reasoner (gemini-pro / deepseek) does the core consult; fast models do the
high-frequency work. Roster scales with the question — quick for factual, full
strength for strategy. Falls back to local seats in local_only mode.
"""
from seats import available_seats, call_seat, extract_json

_MODES = {"brainstorm", "decision", "analysis", "build", "comparison", "factual"}


def quick_mode(idea: str, seat, defaults: dict) -> str:
    sys = ("Classify the question into ONE mode: brainstorm, decision, analysis, build, "
           'comparison, factual. ONLY JSON: {"mode": ""}')
    try:
        d = extract_json(call_seat(seat, [{"role": "system", "content": sys},
                                          {"role": "user", "content": idea}],
                                   defaults, max_tokens=400))
        m = (d or {}).get("mode", "").strip().lower()
        return m if m in _MODES else "analysis"
    except Exception:
        return "analysis"


# Strong but SLOW brains, pulled in (in order) only when the fast council is unsure.
# The escalated brain then leads synthesis. All free-tier (Gemini/NVIDIA/local).
_ESCALATION_PREF = ["gemini-pro", "nvidia-deepseek", "nvidia-heavy",
                    "deepseek-critic", "qwen-generalist"]


def auto_roster(idea: str, cfg) -> tuple[list, list, str]:
    """Return (base_seats, escalation_pool, mode). A dynamic base of ~3 (fast workers +
    a strong chair for non-trivial tasks); the pool holds stronger/extra models brought
    in automatically when the panel is unsure."""
    avail = {s.id: s for s in available_seats(cfg)}
    if not avail:
        return [], [], "analysis"
    fast = avail.get("cerebras-fast") or avail.get("groq-fast") or next(iter(avail.values()))
    mode = quick_mode(idea, fast, cfg.defaults)

    def pick(ids: list) -> list:
        return [avail[i] for i in ids if i in avail]

    # Default base = FAST free models only (cerebras gpt-oss-120b ~2s, groq ~5s,
    # cerebras GLM-4.7) so the common case is quick. Slow strong brains (gemini-pro,
    # deepseek) sit in the escalation pool and are pulled in only when unsure.
    if mode == "factual":            # quick: 2 fast models
        base = pick(["cerebras-fast", "groq-fast"])
    else:                            # brainstorm/decision/build/comparison/analysis
        base = pick(["cerebras-fast", "groq-fast", "cerebras-glm"])

    seen, uniq = set(), []
    for s in base:                   # dedupe, keep order
        if s.id not in seen:
            seen.add(s.id)
            uniq.append(s)
    if len(uniq) < 2:                # never dead-end
        uniq = list(avail.values())[:3]
        seen = {s.id for s in uniq}

    pool = [avail[i] for i in _ESCALATION_PREF if i in avail and i not in seen]
    return uniq, pool, mode
