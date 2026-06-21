"""Fast check that escalation fires on a stall: 2 fast base seats, a fast escalation
seat, low round cap, impossible threshold -> a stall MUST occur and pull in the pool."""
from orchestrator import run_debate
from seats import load_config

cfg = load_config()
cfg.consensus_threshold = 0.99   # impossible -> guarantees an 'unsure' stall
cfg.max_iterations = 3
avail = {s.id: s for s in cfg.seats}
base = [avail["cerebras-fast"], avail["groq-fast"]]          # fast workers
pool = [avail["gemini-chair"]]                               # fast escalation (flash)

idea = "Should I build yet another habit-tracker app?"
mp = run_debate(idea, base, cfg, escalation=pool)
esc = [k for k in mp.proposals if k.startswith("escalation")]
print("rounds:", [round(r["adjusted"], 3) for r in mp.synthesis])
print("ESCALATIONS FIRED:", esc)
print("PASS" if esc else "FAIL (no escalation)")
