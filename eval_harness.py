"""Phase 7 -- eval harness: prove the council beats a single model.

Runs the same idea two ways: (a) one strong model answering alone, (b) the full
council. A judge model (different from the baseline, to avoid self-preference)
scores both blind on a rubric with randomized A/B positions, then picks a winner.
"""
import json
import random
from pathlib import Path

from orchestrator import ask_json, pick_chair, run_debate
from seats import available_seats, load_config

RUBRIC = ["correctness", "depth", "evidence_grounding", "actionability", "risk_awareness"]


def single_model_answer(idea: str, seat, cfg) -> dict:
    system = ("You are a strong expert. Give your single best ranked plan for the problem. "
              'ONLY JSON: {"ranked_plan": ["step", ...], "rationale": ""}')
    data, raw = ask_json(seat, system, f"Problem: {idea}", cfg.defaults)
    return data or {"ranked_plan": [raw[:600]], "rationale": "(unparsed)"}


def _judge_seat(seats: list, baseline):
    """Prefer a judge that is NOT the baseline model (avoid self-preference)."""
    for role in ("chair", "red-teamer", "generalist"):
        for s in seats:
            if s.role == role and s.id != baseline.id:
                return s
    return next((s for s in seats if s.id != baseline.id), baseline)


def judge(idea: str, answer_a: dict, answer_b: dict, judge_seat, cfg) -> dict:
    system = (
        "You are a strict, impartial evaluator. Score answer_A and answer_B independently, "
        "each 0-10 on every dimension: " + ", ".join(RUBRIC) + ". Sum to total (0-50). "
        'Then pick the winner. ONLY JSON: {"A": {' +
        ", ".join(f'"{d}": 0' for d in RUBRIC) + ', "total": 0}, "B": {...same...}, '
        '"winner": "A|B|tie", "rationale": ""}'
    )
    user = json.dumps({"problem": idea, "answer_A": answer_a, "answer_B": answer_b},
                      indent=2, ensure_ascii=False)[:12000]
    data, raw = ask_json(judge_seat, system, user, cfg.defaults)
    return data or {"winner": "tie", "rationale": raw[:400], "A": {}, "B": {}}


def run_eval(idea: str, cfg=None, seats=None, session_path: str | None = None) -> dict:
    cfg = cfg or load_config()
    seats = seats or available_seats(cfg)
    baseline = pick_chair(seats)

    print(f"\n=== EVAL: {idea!r}")
    print(f"baseline (single): {baseline.id}")
    single = single_model_answer(idea, baseline, cfg)

    print("running full council...")
    mp = run_debate(idea, seats, cfg, session_path=session_path)
    council = {"ranked_plan": mp.final.get("ranked_plan", []),
               "confidence": mp.final.get("confidence"),
               "evidence_ratio": mp.final.get("evidence_ratio")}

    # Randomize positions so the judge can't pattern-match A=single.
    flip = random.random() < 0.5
    a, b = (council, single) if flip else (single, council)
    a_is = "council" if flip else "single"
    b_is = "single" if flip else "council"

    jseat = _judge_seat(seats, baseline)
    print(f"judge: {jseat.id}")
    verdict = judge(idea, a, b, jseat, cfg)

    win_label = verdict.get("winner", "tie")
    winner = {"A": a_is, "B": b_is}.get(win_label, "tie")
    result = {
        "idea": idea,
        "baseline_model": baseline.id,
        "judge_model": jseat.id,
        "scores": {a_is: verdict.get("A", {}), b_is: verdict.get("B", {})},
        "winner": winner,
        "rationale": verdict.get("rationale", ""),
        "single_answer": single,
        "council_answer": council,
    }
    print(f"\nwinner: {winner.upper()}")
    print(f"  single  total: {result['scores'].get('single', {}).get('total')}")
    print(f"  council total: {result['scores'].get('council', {}).get('total')}")
    print(f"  rationale: {result['rationale'][:240]}")
    return result


if __name__ == "__main__":
    cfg = load_config()
    avail = {s.id: s for s in available_seats(cfg)}
    want = ["cerebras-fast", "groq-fast", "gemini-chair"]
    seats = [avail[i] for i in want if i in avail] or list(avail.values())[:3]
    idea = "How should a small team choose between a monolith and microservices for a new SaaS?"

    Path("sessions").mkdir(exist_ok=True)
    res = run_eval(idea, cfg, seats, session_path="sessions/eval_demo.json")
    Path("sessions/eval_demo_result.json").write_text(
        json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nsaved -> sessions/eval_demo_result.json")
