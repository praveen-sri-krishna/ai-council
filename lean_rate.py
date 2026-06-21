"""Lean rating: run ONE complicated question through the new auto mode, judge the
result on an absolute 0-10 rubric (2 judges), and contrast with the already-saved
OLD answer for the same question (no recompute). 1 debate, not 8."""
import json
from pathlib import Path

from orchestrator import run_debate
from router import auto_roster
from seats import Seat, call_seat, extract_json, load_config

CFG = load_config()
IDEA = "How did Cursor become so successful, and what would be the next Cursor I should build?"
OLD = "sessions/session_20260620_213016.json"   # pre-upgrade answer (the weak ~5/10 one)

JUDGES = [
    Seat(id="judge-glm", provider="cerebras", role="judge", personality="",
         base_url="https://api.cerebras.ai/v1", model="zai-glm-4.7", key_env="CEREBRAS_API_KEY"),
    Seat(id="judge-oss", provider="cerebras", role="judge", personality="",
         base_url="https://api.cerebras.ai/v1", model="gpt-oss-120b", key_env="CEREBRAS_API_KEY"),
]
RUBRIC = ("Rate this council answer for a founder, 0-10 on each: directly_answers, depth, "
          "concreteness, honesty (willing to say pivot/drop), evidence_grounding, differentiators, "
          "actionability. Give overall_/10 too. ONLY JSON: "
          '{"directly_answers":0,"depth":0,"concreteness":0,"honesty":0,"evidence_grounding":0,'
          '"differentiators":0,"actionability":0,"overall_10":0,"one_line":""}')


def rate(answer: dict):
    scores = []
    for j in JUDGES:
        d = extract_json(call_seat(j, [{"role": "system", "content": RUBRIC},
                                       {"role": "user", "content": json.dumps(answer, ensure_ascii=False)[:12000]}],
                                   CFG.defaults))
        if d and "overall_10" in d:
            scores.append(d)
            print(f"  {j.id}: overall={d.get('overall_10')}/10 | {str(d.get('one_line',''))[:120]}")
    if scores:
        avg = sum(float(s["overall_10"]) for s in scores) / len(scores)
        print(f"  => avg {avg:.1f}/10")
        return avg
    return None


def main():
    base, pool, mode = auto_roster(IDEA, CFG)
    print(f"AUTO: mode={mode} base={[s.id for s in base]} escalation={[s.id for s in pool][:3]}\n")
    mp = run_debate(IDEA, base, CFG, escalation=pool)
    f = mp.final
    new = {"direct_answer": f.get("direct_answer"), "verdict": f.get("verdict"),
           "key_points": f.get("key_points"), "differentiators": f.get("differentiators"),
           "competitors": f.get("competitors"), "evidence_ratio": f.get("evidence_ratio")}

    old_f = json.loads(Path(OLD).read_text(encoding="utf-8")).get("final", {})
    old = {"ranked_plan": old_f.get("ranked_plan"), "direct_answer": old_f.get("direct_answer"),
           "verdict": old_f.get("verdict")}

    print("\n--- OLD answer (pre-upgrade) rating ---")
    old_score = rate(old)
    print("\n--- NEW auto-mode answer rating ---")
    new_score = rate(new)
    print(f"\n=== {('OLD %.1f -> NEW %.1f' % (old_score or 0, new_score or 0))} ===")
    esc = [k for k in mp.proposals if k.startswith("escalation")]
    print(f"escalations fired: {esc} | rounds: {[round(r['adjusted'],3) for r in mp.synthesis]}")
    print("NEW verdict:", (f.get('verdict') or {}).get('call'), "| title:", f.get('title'))


if __name__ == "__main__":
    main()
