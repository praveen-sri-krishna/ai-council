# AI Council — Project Context

Local-first multi-model think tank. Give it an idea; a panel of LLMs propose
independently, critique each other, web-check claims, then synthesize a ranked
plan + a minority report. Runs on this PC (RTX 4080) + free hosted APIs.
**Must cost ~$0 to run.** Claude Code is the builder, not part of the runtime.

## Stack
- Python 3.11+, `uv` for env/deps
- Ollama (local seats) + OpenAI-compatible hosted seats (NVIDIA NIM, Groq, Gemini, OpenRouter)
- SearXNG (Docker) for web search
- Memento (cross-session case memory) + JSON "Memory Palace" (within-debate state)
- Gradio UI on localhost · Windows host

## Architecture — the whole spine. Do not add layers beyond this.
- `seats.py` — `Seat = {id, provider, base_url, model, key_env, role, personality}`.
  ONE OpenAI-compatible `call_seat()` serves every provider, local and hosted.
- `orchestrator.py` — loop: propose → critique → web-ground → revise → synthesize → vote (to threshold). Emits ranked plan + dissent.
- `memory.py` — Memory Palace (dataclass→JSON) + Memento read/write of past debates as cases.
- `research.py` — SearXNG queries; separate supporting + counter-evidence passes.
- `config.yaml` — seats, thresholds, search provider, privacy mode.
- `gui.py` — Gradio at 127.0.0.1:7860.

## Hard rules
- STAY LEAN. No agent framework (no CrewAI/LangChain/AG2). Plain Python + `openai` SDK + `requests`. If a class isn't reused, don't write it.
- Every model call goes through `call_seat()`. Local seats set `keep_alive: 0` so only one model holds VRAM at a time.
- Hosted calls: handle HTTP 429 with exponential backoff + failover to the next provider. A debate must never die on a rate limit.
- Model outputs are strict JSON, always parsed through a tolerant `extract_json()` fallback.
- Privacy mode `local_only`: no network seats, no search egress. Default to it for unpublished ideas.
- Degrade gracefully: if a provider key is missing, skip that seat, don't crash.
- Never hardcode keys or model tags. Keys from env. Confirm live Ollama tags / NIM model strings at build time — don't trust names from memory.

## Before coding
- Read reference repo `geek-alt/LLM-Council` (`orchestrator.py`) for the loop + Memory Palace pattern. Borrow the 4 ideas (keep_alive:0 swap, JSON Memory Palace, consensus loop, minority report). Do NOT copy its structure wholesale.
- Memento is already installed — read its actual API from the installed package before wiring `memory.py`. Don't assume method names.

## Workflow
- Build in the phase order in `BUILD_BRIEF.md`, one phase at a time, run before moving on.
- After each phase: smoke test, then stop and summarize what changed.
