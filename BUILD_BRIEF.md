# BUILD BRIEF — AI Council (hand this to Claude Code as the opening prompt)

## What we're building
A local-first, browser-based "think tank." I give it an idea or problem; a panel
of different LLMs each propose a plan independently, then critique each other to
surface flaws, verify shaky factual claims via web search, and finally a chair
model synthesizes a single ranked plan plus a "minority report" of unresolved
disagreements. It runs on my PC (RTX 4080, 32GB, Windows) using local models via
Ollama plus free hosted APIs. It must cost ~$0 to operate.

Read `CLAUDE.md` first — it holds the architecture and hard rules. Then read the
reference repo `geek-alt/LLM-Council` (`orchestrator.py`) to see the debate loop
and Memory Palace pattern. Borrow its 4 good ideas; do not copy its structure.

## Seats (the model panel)
A "seat" is one participant. All seats — local and hosted — are called through a
single OpenAI-compatible `call_seat()`. The only difference between them is
`base_url`, `model`, and which env var holds the key (local needs no key).

Roster to start with (make it fully config-driven so I can change it):

| Seat | Provider | Role | Notes |
|------|----------|------|-------|
| qwen3:14b | Ollama (local) | generalist | my daily driver; fast resident voice |
| gemma (4, ~26B/31B) | Ollama (local) | dissenter | Google lineage = genuinely different priors |
| deepseek-r1:14b | Ollama (local) | red-teamer/critic | built to reason about where plans break |
| big hosted model | NVIDIA NIM | heavyweight | a large model my GPU can't run; free |
| fast Llama/Qwen | Groq | fast seat | speed keeps debate rounds snappy |
| Gemini 2.5 Flash | Google AI Studio | researcher + chair | 1M context for ingesting search; strong synthesis |
| Kimi (K2.x) | OpenRouter | agentic builder | turns the agreed plan into concrete build steps |

Env vars: `NVIDIA_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`.
Confirm the exact current model strings at build time (NIM/Groq/OpenRouter catalogs
and my installed Ollama tags) — do not hardcode names from training data.

Local seats: set `keep_alive: 0` so each unloads after speaking and only one model
occupies VRAM at a time. Hosted seats run in parallel (no VRAM contention).

## Debate protocol (the orchestrator loop)
0. **Research** — a light seat turns my prompt into SearXNG queries; results stored.
1. **Propose** — each seat answers the idea cold (no peeking), with its own role/personality.
2. **Critique** — each seat reads all proposals and returns, as JSON: a 0–1 score per
   proposal + structured critique (strengths, weaknesses, hidden assumptions, failure modes).
3. **Web-ground** — claims flagged "needs verification" trigger a search pass
   (supporting AND counter-evidence); results injected back.
4. **Revise** — each seat revises its plan given critiques + evidence.
5. **Synthesize** — the chair seat merges the best surviving ideas into ONE ranked
   plan and explicitly lists disagreements that did not resolve.
6. **Vote** — seats score the synthesis. ≥ threshold (e.g. 0.95) → done; else feed
   critiques back to step 5. Hard cap on iterations; return best-scoring if hit.

Final output: ranked plan + dissent section + confidence + cited sources.

## Memory
- **Within a debate:** the Memory Palace — a dataclass (prompt, research, proposals,
  critiques, scores, synthesis) serialized to JSON, injected into each seat's context.
  Compress the running log to a summary once it exceeds ~15 entries.
- **Across debates (Memento):** after each run, write a case (idea, final plan, key
  critiques, and later the real outcome) to Memento. Before a new debate, retrieve
  top-k similar past cases and inject them so the council improves over time.
  Read Memento's actual installed API before wiring this — don't assume method names.

## Web search
SearXNG in Docker, local. Two passes per flagged claim: one for supporting evidence,
one for counter-evidence, so the panel can't all agree on something false. Make the
provider swappable (SearXNG default; DuckDuckGo keyless fallback).

## Privacy + cost
- `privacy_mode: local_only` → local seats only, no search egress. Default for
  unpublished ideas. `privacy_mode: open` → bring in hosted seats.
- Track requests-per-minute / per-day per provider; on 429, back off and fail over
  to the next lane. Cache research results so a repeated query doesn't re-spend quota.

## File layout
```
ai-council/
  config.yaml        # seats, thresholds, search provider, privacy mode
  seats.py           # Seat model + call_seat() (one adapter for all providers)
  orchestrator.py    # the debate loop
  memory.py          # Memory Palace + Memento integration
  research.py        # SearXNG / DuckDuckGo
  gui.py             # Gradio UI at 127.0.0.1:7860
  sessions/          # JSON snapshots for replay (gitignored)
  README.md
```

## Build phases — do these in order, run each before the next
1. **Seats + adapter.** `seats.py` + `config.yaml`. Prove `call_seat()` works against
   one local (Ollama) and one hosted (whichever key I have) seat. Smoke test, stop.
2. **Core loop, no search/memory yet.** propose → critique → synthesize → vote on 3
   local seats. Print the Memory Palace JSON. Stop.
3. **Web grounding.** SearXNG + the research/flag/inject steps.
4. **Memory.** Memory Palace persistence + Memento cases.
5. **Routing hardening.** 429 backoff, provider failover, graceful skip on missing keys, privacy_mode.
6. **GUI + replay.** Gradio: live phase trace, final answer, dissent, saved-session browser.
7. **Eval harness.** Run one idea through a single model vs. the full council; compare on a rubric so I can confirm the council is actually better.

## Do NOT
- Do not pull in CrewAI / LangChain / AG2. Plain Python only.
- Do not invent abstractions used once. Do not build for scale/multi-user — this is personal.
- Do not put any paid or Claude-API dependency in the runtime. The council runs on free/local only.
- Do not hardcode keys or model names. Do not let one provider's outage/limit break a run.

Start with Phase 1. After each phase, give me a one-paragraph summary and wait.
