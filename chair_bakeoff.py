"""Chair bake-off: which FREE reasoning model is the best orchestrator?

Take one rich debate state (proposals+critiques+evidence already there), have each
candidate model run the SAME synthesis, then a neutral judge ranks the reasoning.
Isolates the chair's reasoning — apples-to-apples, no full re-debates."""
import json
import time
from pathlib import Path

from memory import MemoryPalace
from orchestrator import phase_synthesize
from seats import Seat, call_seat, extract_json, load_config

CFG = load_config()
PERSONA = "Synthesizer. Rigorous, decisive, concrete; resolves disagreement with evidence."

CANDIDATES = [
    Seat(id="gemini-2.5-flash (current)", provider="gemini", role="chair", personality=PERSONA,
         base_url="https://generativelanguage.googleapis.com/v1beta/openai",
         model="gemini-2.5-flash", key_env="GEMINI_API_KEY"),
    Seat(id="gemini-2.5-pro", provider="gemini", role="chair", personality=PERSONA,
         base_url="https://generativelanguage.googleapis.com/v1beta/openai",
         model="gemini-2.5-pro", key_env="GEMINI_API_KEY"),
    Seat(id="cerebras gpt-oss-120b", provider="cerebras", role="chair", personality=PERSONA,
         base_url="https://api.cerebras.ai/v1", model="gpt-oss-120b", key_env="CEREBRAS_API_KEY"),
    Seat(id="nvidia deepseek-v4-pro", provider="nvidia", role="chair", personality=PERSONA,
         base_url="https://integrate.api.nvidia.com/v1",
         model="deepseek-ai/deepseek-v4-pro", key_env="NVIDIA_API_KEY"),
    Seat(id="nvidia nemotron-ultra-253b", provider="nvidia", role="chair", personality=PERSONA,
         base_url="https://integrate.api.nvidia.com/v1",
         model="nvidia/llama-3.1-nemotron-ultra-253b-v1", key_env="NVIDIA_API_KEY"),
    Seat(id="openrouter deepseek-r1", provider="openrouter", role="chair", personality=PERSONA,
         base_url="https://openrouter.ai/api/v1", model="deepseek/deepseek-r1",
         key_env="OPENROUTER_API_KEY"),
]


def load_mp(path: str) -> MemoryPalace:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    mp = MemoryPalace(prompt=d["prompt"])
    mp.research = d.get("research", {})
    mp.proposals = d.get("proposals", {})
    mp.critiques = d.get("critiques", {})
    return mp


def main():
    mp = load_mp("sessions/session_20260621_103509.json")  # India import debate (rich state)
    print(f"Bake-off on: {mp.prompt[:80]}\n")
    results = []
    for c in CANDIDATES:
        t0 = time.time()
        try:
            synth = phase_synthesize(c, mp, None, CFG.defaults)
            dt = time.time() - t0
            ok = bool(synth.get("direct_answer")) and synth.get("rationale") != "(unparsed)"
            results.append({"chair": c.id, "synth": synth, "secs": round(dt, 1), "ok": ok})
            print(f"[{'OK ' if ok else 'FAIL'}] {c.id:32} {dt:5.1f}s  "
                  f"verdict={synth.get('verdict',{}).get('call')} "
                  f"diffs={len(synth.get('differentiators',[]))}")
        except Exception as e:
            print(f"[ERR ] {c.id:32} {type(e).__name__}: {str(e)[:70]}")

    good = [r for r in results if r["ok"]]
    if len(good) < 2:
        print("Not enough valid syntheses to judge."); return

    # Neutral judge ranks anonymized syntheses on reasoning quality.
    labels = [chr(65 + i) for i in range(len(good))]
    anon = {labels[i]: {"direct_answer": g["synth"].get("direct_answer"),
                        "verdict": g["synth"].get("verdict"),
                        "key_points": g["synth"].get("key_points"),
                        "differentiators": g["synth"].get("differentiators"),
                        "competitors": g["synth"].get("competitors")} for i, g in enumerate(good)}
    judge = Seat(id="judge", provider="cerebras", role="judge", personality="",
                 base_url="https://api.cerebras.ai/v1", model="zai-glm-4.7",
                 key_env="CEREBRAS_API_KEY")
    jsys = ("You are a strict reasoning judge. Score each answer 0-10 on: depth_of_reasoning, "
            "concreteness, honesty_of_verdict (willing to say pivot/drop), differentiator_sharpness, "
            "no_buzzwords. Sum to total (0-50). ONLY JSON: "
            '{"<LABEL>": {"depth_of_reasoning":0,"concreteness":0,"honesty_of_verdict":0,'
            '"differentiator_sharpness":0,"no_buzzwords":0,"total":0}, ..., "winner":"<LABEL>", "why":""}')
    jt, _ = (lambda: ( (lambda txt: (extract_json(txt), txt))(
        call_seat(judge, [{"role": "system", "content": jsys},
                          {"role": "user", "content": json.dumps(anon, ensure_ascii=False)[:14000]}],
                  CFG.defaults)) ))()
    print("\n=== JUDGE ===")
    if jt:
        for i, lab in enumerate(labels):
            sc = jt.get(lab, {})
            print(f"  {lab} = {good[i]['chair']:32} total={sc.get('total')} ({good[i]['secs']}s)")
        w = jt.get("winner")
        wi = labels.index(w) if w in labels else None
        print(f"\nWINNER: {w} -> {good[wi]['chair'] if wi is not None else '?'}")
        print("WHY:", str(jt.get("why",""))[:300])
    else:
        print("judge did not parse")


if __name__ == "__main__":
    main()
