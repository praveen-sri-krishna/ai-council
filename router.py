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


# Strongest reasoners to pull in (in order) when the council is unsure.
_ESCALATION_PREF = ["nvidia-deepseek", "gemini-pro", "nvidia-heavy", "kimi-builder",
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

    # Default base = 3 (tiered: fast workers + a strong chair). Stronger models are
    # held in the escalation pool and pulled in only when the council is unsure.
    if mode == "factual":            # quick: 3 fast models, no heavy chair needed
        base = pick(["cerebras-fast", "groq-fast", "gemini-chair"])
    elif mode == "brainstorm":       # divergence: a dissenter for spread + strong chair
        base = pick(["cerebras-fast", "gemma-dissenter", "gemini-pro"])
    else:                            # decision/build/comparison/analysis
        base = pick(["cerebras-fast", "groq-fast", "gemini-pro"])

    if mode != "factual" and "gemini-pro" in avail and avail["gemini-pro"] not in base:
        base.append(avail["gemini-pro"])
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
