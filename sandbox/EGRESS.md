# Network egress this loop needs — for a deny-by-default sandbox (NVIDIA OpenShell / NemoClaw)

The agent must reach exactly six hosts (one more is optional, see below). Everything else stays closed. Enter these into the sandbox's
network policy; the loop has no other outbound calls (verified: `grep -rn "https://" loop/`; the gRPC host is in `loop/stt.py` and `loop/tts.py`).

| Host | Purpose | Method | Auth |
|---|---|---|---|
| `integrate.api.nvidia.com` | Nemotron inference (NIM, OpenAI-compatible) | POST `/v1/chat/completions` | `NVIDIA_API_KEY` — kept on the host, injected as env, never written to cache or logs |
| `grpc.nvcf.nvidia.com` | Speech-to-text — interview audio → transcript (Riva gRPC over HTTP/2, port 443; Parakeet 1.1B multilingual or Whisper Large v3) · text-to-speech for the collection demo (Chatterbox Multilingual, a synthetic script → the audio STT then hears) | gRPC `StreamingRecognize` / `Recognize` · `SpeechSynthesisService.Synthesize` | `NVIDIA_API_KEY` + NVCF `function-id` as gRPC metadata — same rules as above. The proxy must let HTTP/2 through its tunnel |
| `eutils.ncbi.nlm.nih.gov` | PubMed E-utilities (esearch, efetch) | GET | none |
| `clinicaltrials.gov` | ClinicalTrials.gov API v2 | GET `/api/v2/studies` | none |
| `api.fda.gov` | openFDA drug label + FAERS events | GET | none |
| `data.cms.gov` | Medicare Part D spending by drug | GET `/data-api/v1/dataset/…/data` | none |
| `raw.githubusercontent.com` *(optional)* | «GitHub 원본과 대조» on the collection page — compares each local script file with the same path on GitHub by sha256; nothing fetched is written | GET | none |

Filesystem: read `data/contract.json`, `data/field_notes.json`, `loop/prompts/`; write only under `data/`
(`transcribe` and `collect` append to `data/field_notes.json`; an uploaded recording goes to a temp dir and is deleted, only its hash is kept; synthesised audio and live listening records go to `data/tts_cache/` and `data/collect_runtime/`).

Why this matters for this loop specifically: the agent reads field notes (sensitive in a real deployment),
receives text from the open web (untrusted), and can write. That is the combination a sandbox cannot fix
on its own — so the loop also (a) verifies every quote against its source in code, (b) stores no number
the model produced, and (c) advances nothing without a named human signature.

This demo was developed on macOS, where OpenShell does not run; the policy above is what a NemoClaw
deployment on DGX / Linux should apply. It has not been executed inside OpenShell yet.
