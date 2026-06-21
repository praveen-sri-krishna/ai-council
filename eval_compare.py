"""Measured re-rating: new auto-routed council (tiered + escalation) vs a single
strong model, across varied questions, scored head-to-head by two judges."""
import json
import random
from pathlib import Path

from orchestrator import ask_json, run_debate
from router import auto_roster
from seats import Seat, call_seat, extract_json, load_config

CFG = load_config()
QUESTIONS = [
    "Should a solo founder build an AI tool for restaurant inventory management?",
    "What's the best go-to-market for a niche B2B SaaS with no marketing budget?",
    "Is it worth building a privacy-first alternative to Notion in 2026?",
    "How should a small team decide between React Native and native iOS/Android?",
]
SINGLE = Seat(id="single-gemini-pro", provider="gemini", role="solo",
              personality="A strong expert giving your single best answer.",
              base_url="https://generativelanguage.googleapis.com/v1beta/openai",
              model="gemini-2.5-pro", key_env="GEMINI_API_KEY")
JUDGES = [
    Seat(id="judge-glm", provider="cerebras", role="judge", personality="",
         base_url="https://api.cerebras.ai/v1", model="zai-glm-4.7", key_env="CEREBRAS_API_KEY"),
    Seat(id="judge-oss", provider="cerebras", role="judge", personality="",
         base_url="https://api.cerebras.ai/v1", model="gpt-oss-120b", key_env="CEREBRAS_API_KEY"),
]


def single_answer(idea):
    sys = ('Give your single best answer. ONLY JSON: {"direct_answer":"","key_points":[],'
           '"verdict":{"call":"","why":""}}')
    d, raw = ask_json(SINGLE, sys, f"Problem: {idea}", CFG.defaults)
    return d or {"direct_answer": raw[:600]}


def judge(idea, a, b):  # a,b anonymized; returns winner label + scores
    rub = ("Which answer is more useful, honest, concrete, and decisive? Score X and Y 0-10. "
           'ONLY JSON: {"X":{"total":0},"Y":{"total":0},"winner":"X|Y"}')
    votes = []
    for j in JUDGES:
        d = extract_json(call_seat(j, [{"role": "system", "content": rub},
                                       {"role": "user", "content": json.dumps({"problem": idea, "X": a, "Y": b}, ensure_ascii=False)[:13000]}],
                                   CFG.defaults))
        if d:
            votes.append(d)
    return votes


def main():
    council_wins = single_wins = 0
    rows = []
    for q in QUESTIONS:
        base, pool, mode = auto_roster(q, CFG)
        mp = run_debate(q, base, CFG, escalation=pool)
        c = {"direct_answer": mp.final.get("direct_answer"), "verdict": mp.final.get("verdict"),
             "key_points": mp.final.get("key_points"), "differentiators": mp.final.get("differentiators")}
        s = single_answer(q)
        flip = random.random() < 0.5
        X, Y = (c, s) if not flip else (s, c)
        council_is = "X" if not flip else "Y"
        votes = judge(q, X, Y)
        cw = sum(1 for v in votes if v.get("winner") == council_is)
        sw = len(votes) - cw
        council_wins += cw
        single_wins += sw
        winner = "COUNCIL" if cw > sw else ("SINGLE" if sw > cw else "tie")
        rows.append(f"[{mode:10}] {winner:7} (council {cw}/{len(votes)} judge votes) | {q[:50]}")
        print(rows[-1])
    print("\n=== RESULT ===")
    for r in rows:
        print(r)
    total = council_wins + single_wins
    print(f"\nCouncil judge-votes: {council_wins}/{total} = {round(100*council_wins/max(total,1))}% win rate")
    Path("sessions/eval_compare_result.json").write_text(
        json.dumps({"council_wins": council_wins, "single_wins": single_wins, "rows": rows},
                   indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
