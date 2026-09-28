---
name: evidence-loop
description: Screen a drug hypothesis (off-label demand, off-label use, repurposing, dosing, safety) against public evidence — PubMed, ClinicalTrials.gov, openFDA label + FAERS, CMS Medicare Part D — with verbatim evidence pointers and code-computed tallies. Use when asked whether field signals about a drug are backed, contradicted, or untouched by public evidence, or to run the full loop from field notes to an approved follow-up checklist. Never counts with the model; never advances past a human signature.
---

# evidence-loop

An agent skill for the Agent Skills spec (works in Claude Code, OpenClaw and compatible clients).
Runtime model: NVIDIA Nemotron 3 Ultra via NIM (`integrate.api.nvidia.com/v1`, OpenAI-compatible).
Speech-to-text: build.nvidia.com hosted ASR via Riva gRPC (`grpc.nvcf.nvidia.com`) — `nvidia/parakeet-1.1b-rnnt-multilingual-asr` (default, ko-KR, streaming; word boosting off unless `STT_BOOST` > 0 — it invented segment names) or `openai/whisper-large-v3` (`--engine whisper`).
Text-to-speech (collection demo only): `resembleai/chatterbox-multilingual-tts` on the same gRPC host, voice `Chatterbox-Multilingual.ko-KR.Male` — a synthetic interview script is read aloud, a person listens while STT listens to the same file, and the transcript becomes a field note.

## When to use
- "Is there public evidence for <drug> in <patient group>?"
- "What did the field say about <drug>, and does the literature agree?"
- "Run the loop" — field notes → claims → hypotheses → screen → human review → board → field checklist.

## Commands (run from the repo root)
```
uv run python -m loop.cli transcribe <audio> --hcp <ref> --specialty <text> --date YYYY-MM-DD --consent-by "<name>"
                                                # interview audio → Parakeet multilingual (ko-KR) or Whisper transcript → one field note
uv run python -m loop.cli scripts               # synthetic interview scripts in data/field_scripts/ with their intent and the current count
uv run python -m loop.cli collect FS-01 --consent-by "<name>"   # script → Chatterbox TTS → Parakeet transcript (CER vs script) → one field note
uv run python -m loop.cli uncollect             # undo: only script-collected notes and what was derived from them; refuses after a signature
uv run python -m loop.cli sense                 # field notes → claims (verbatim quotes verified by code) → hypotheses over threshold
uv run python -m loop.cli hypotheses
uv run python -m loop.cli screen HYP-001        # 3 agents (PubMed / CT.gov / label) + FAERS and Part D numbers
uv run python -m loop.cli review HYP-001 --by "<name>"    # gate 1 — a person signs that they read the evidence
uv run python -m loop.cli board HYP-001         # AI Board: orchestrator + 7 executives, ~20 turns in parallel; refuses without gate 1
uv run python -m loop.cli approve HYP-001 --by "<name>"   # gate 2 — questions become the next field checklist
```

## Rules the skill enforces (in code, not in prompts)
1. Every number shown is computed in code; the model selects and quotes.
2. A quote is kept only if it is found verbatim in the record the model saw; otherwise it is shown as dropped, never counted.
3. Adverse-event candidates go to a separate safety queue and never enter the analytics tally.
4. `NO_EXTERNAL_EVIDENCE` → no ranking. A person decides what silence means.
5. A DEVELOPMENT (off-label) hypothesis routes to expert review only; commercial actions are never proposed.
6. Two human gates: review signature before the board, approval before any action item exists.
7. The board's recommendation is a code tally of the executives' final positions (confidence-weighted, lead ×1.5); the orchestrator only writes the minutes. Citations in speech count only if they name an evidence id the reviewer saw; a commercial action proposed on an off-label hypothesis is blocked and recorded.

## Outputs
- `data/state.json` — claims, safety queue, hypotheses, screens, reviews, board memos, actions (system of record)
- `data/field_checklist.json` — open questions for the next interview
- `data/cache/` (public API responses) and `data/llm_cache/` (model outputs) — same input, same output, replayable without a key
- `data/llm_runs.jsonl` — every model call with purpose, model, tokens, cache hit
- `data/stt_runs.jsonl`, `data/stt_cache/` — every transcription (audio hash, seconds, cache hit); local only, never committed
- `data/field_scripts/` — the synthetic scripts (FS-NN.json) and the committed bake: `audio/` (the MP3 both the listener and STT hear), `stt/` (its transcript), `bake.json` (hashes, CER, cache keys) — the collection chain replays without a key
- `data/tts_cache/`, `data/tts_runs.jsonl`, `data/collect_runtime/` — synthesis cache, run log, live listening records; local only
