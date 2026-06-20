# AI Council

A **local-first, multi-model think tank**. Give it an idea; a panel of LLMs
propose independently, critique each other, web-check shaky claims, and converge
on a single ranked plan plus a minority report. Runs on a local GPU (Ollama) +
free hosted APIs. Designed to cost ~$0 to operate.

![status](https://img.shields.io/badge/status-working-3ecf8e) ![python](https://img.shields.io/badge/python-3.11%2B-blue)

## What it does

A debate runs through these phases:

1. **Research / prior-art** — turns the idea into searches; checks GitHub for
   existing tools so the panel builds on proven work instead of reinventing it.
2. **Propose** — each seat answers cold, in its own role/personality.
3. **Critique** — seats score and pick apart each other's proposals.
4. **Web-ground** — flagged claims get a *supporting* and a *counter* search;
   a fact-checker marks each `supported` / `refuted` / `uncertain`.
5. **Revise** — seats fix their plans against the critiques and evidence.
6. **Synthesize → vote** — the chair folds a concrete fix for every objection
   into one ranked plan; the panel votes. The consensus score is **monotonic
   (never decreases)** and an **out-of-the-box pass** fires if it stalls.

Final output: a ranked plan, the fix-per-objection log, an evidence ledger,
a model **leaderboard**, and a **minority report**.

## Highlights

- **One adapter for every provider** — local Ollama + 5 free hosted lanes
  (Cerebras, Groq, NVIDIA NIM, Gemini, OpenRouter) through a single `call_seat()`.
- **VRAM-safe local seats** — `keep_alive:0` + capped `num_ctx` keep models 100%
  on a 16GB GPU.
- **Routing hardening** — retry + exponential backoff on 429/5xx, graceful skip
  on missing keys, and chair failover, so a debate never dies on a rate limit.
- **Cross-debate memory** — each run is stored as a case (Memento); future
  debates recall similar cases and improve.
- **Live GUI** ("Situation Room") — stream the deliberation, watch the consensus
  meter climb, then **chat 1:1 with the Leader** (the best-performing model), who
  answers directly or takes your question back to the group.
- **Eval harness** — single model vs. full council, scored by a judge on a rubric.

## Architecture

| File | Role |
|------|------|
| `seats.py` | `Seat` model + one OpenAI-compatible `call_seat()` (retry/backoff, `extract_json`) |
| `orchestrator.py` | the debate loop + event stream |
| `research.py` | keyless web search (DuckDuckGo) + GitHub prior-art, cached |
| `memory.py` | within-debate Memory Palace + Memento cross-debate cases |
| `leader.py` | the Leader chat (answer vs. consult-group) |
| `gui.py` | Gradio "Situation Room" UI |
| `eval_harness.py` | single-model-vs-council evaluation |
| `config.yaml` | seats, thresholds, privacy mode, search provider |

## Setup

```bash
uv sync
cp .env.example .env      # paste your free API keys (any subset; missing = skipped)
```

Local seats need [Ollama](https://ollama.com) running with the models in
`config.yaml` (e.g. `ollama pull qwen3:14b`).

## Run

```bash
uv run python orchestrator.py     # a debate in the terminal
uv run python gui.py              # the Situation Room UI -> http://127.0.0.1:7860
uv run python eval_harness.py     # single model vs. council
```

Set `privacy_mode: local_only` in `config.yaml` for fully offline runs (local
seats only, no search egress; Memento case memory still works — it's local SQLite).

## Cost

Built to run on free local + free-tier hosted models. No paid runtime dependency.
