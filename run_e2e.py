"""E2E check: a full debate using the API models not yet exercised in a real run
(nvidia-heavy + kimi-builder), plus chair. Confirms the whole pipeline with them."""
from orchestrator import run_debate
from seats import available_seats, load_config

cfg = load_config()
avail = {s.id: s for s in available_seats(cfg)}
want = ["nvidia-heavy", "kimi-builder", "gemini-chair", "cerebras-fast"]
seats = [avail[i] for i in want if i in avail]
print("E2E seats:", [s.id for s in seats])

idea = "Recommend an architecture for a real-time collaborative document editor for small teams."
mp = run_debate(idea, seats, cfg, session_path="sessions/e2e_test.json")
f = mp.final
print("\nE2E RESULT:")
print("  confidence:", f.get("confidence"))
print("  rounds:", [r["adjusted"] for r in mp.synthesis])
print("  evidence:", f.get("evidence_counts"), "ratio", f.get("evidence_ratio"))
print("  leaderboard:", f.get("leaderboard"))
print("  plan steps:", len(f.get("ranked_plan", [])))
ok = bool(f.get("ranked_plan")) and f.get("confidence") is not None
print("  E2E", "PASS" if ok else "FAIL")
