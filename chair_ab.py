"""Settle it: DeepSeek V4 Pro vs gemini-2.5-pro as chair, same debate, 2 judges."""
import json
from pathlib import Path

from memory import MemoryPalace
from orchestrator import phase_synthesize
from seats import Seat, call_seat, extract_json, load_config

CFG = load_config()
P = "Lead synthesizer. Rigorous, decisive, resolves disagreement with evidence."

A = Seat(id="gemini-2.5-pro", provider="gemini", role="chair", personality=P,
         base_url="https://generativelanguage.googleapis.com/v1beta/openai",
         model="gemini-2.5-pro", key_env="GEMINI_API_KEY")
B = Seat(id="deepseek-v4-pro", provider="nvidia", role="chair", personality=P,
         base_url="https://integrate.api.nvidia.com/v1",
         model="deepseek-ai/deepseek-v4-pro", key_env="NVIDIA_API_KEY")

JUDGES = [
    Seat(id="judge-gpt-oss", provider="cerebras", role="judge", personality="",
         base_url="https://api.cerebras.ai/v1", model="gpt-oss-120b", key_env="CEREBRAS_API_KEY"),
    Seat(id="judge-glm", provider="cerebras", role="judge", personality="",
         base_url="https://api.cerebras.ai/v1", model="zai-glm-4.7", key_env="CEREBRAS_API_KEY"),
]


def load_mp(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    mp = MemoryPalace(prompt=d["prompt"])
    mp.research, mp.proposals, mp.critiques = d.get("research", {}), d.get("proposals", {}), d.get("critiques", {})
    return mp


def main():
    mp = load_mp("sessions/session_20260621_103509.json")
    out = {}
    for seat in (A, B):
        try:
            s = phase_synthesize(seat, mp, None, CFG.defaults)
            out[seat.id] = {"direct_answer": s.get("direct_answer"), "verdict": s.get("verdict"),
                            "key_points": s.get("key_points"), "differentiators": s.get("differentiators"),
                            "competitors": s.get("competitors")}
            print(f"[ok] {seat.id}: verdict={s.get('verdict',{}).get('call')} diffs={len(s.get('differentiators',[]))}")
        except Exception as e:
            print(f"[ERR] {seat.id}: {e}")
            return

    # anonymize X/Y (randomize-free: fixed but judges don't know which is which)
    anon = {"X": out["gemini-2.5-pro"], "Y": out["deepseek-v4-pro"]}
    rub = ("Judge which synthesis reasons better. Score X and Y 0-10 on depth, concreteness, "
           "honesty (willing to pivot/drop), differentiator sharpness, no-buzzwords. ONLY JSON: "
           '{"X":{"total":0},"Y":{"total":0},"winner":"X|Y","why":""}')
    print("\n=== JUDGES (X=gemini-2.5-pro, Y=deepseek-v4-pro) ===")
    for j in JUDGES:
        try:
            d = extract_json(call_seat(j, [{"role": "system", "content": rub},
                                           {"role": "user", "content": json.dumps(anon, ensure_ascii=False)[:14000]}],
                                       CFG.defaults))
            if d:
                print(f"  {j.id}: X={d.get('X',{}).get('total')} Y={d.get('Y',{}).get('total')} "
                      f"winner={d.get('winner')} | {str(d.get('why',''))[:140]}")
            else:
                print(f"  {j.id}: (no parse)")
        except Exception as e:
            print(f"  {j.id}: ERR {e}")


if __name__ == "__main__":
    main()
