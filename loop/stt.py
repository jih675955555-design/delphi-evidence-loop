"""The single wrapper every speech-to-text call goes through — NVIDIA-hosted ASR on build.nvidia.com.

This is the entrance the loop did not have: interview audio → transcript → one entry in data/field_notes.json.
From there `sense` reads it exactly like the synthetic notes, so the rule «every quote has a position in the
source» now reaches back to the recording — the transcript *is* the source text.

Same rules as llm.py:
1. Every call is cached on disk by the audio's hash — same recording, same transcript, no second API call —
   and logged to data/stt_runs.jsonl (model, language, seconds of audio, cache hit).
2. The key never leaves the host: NVIDIA_API_KEY from .env, never written to logs or cache.
3. An empty transcript is an error, never a cached result.

Two engines, both Riva gRPC on NVCF (grpc.nvcf.nvidia.com:443), chosen with STT_ENGINE or `--engine`:
- parakeet (default) — nvidia/parakeet-1.1b-rnnt-multilingual-asr. NVIDIA's own model, 25 languages incl. ko-KR,
  streaming, word boosting with the contract's vocabulary.
- whisper — openai/whisper-large-v3. Strong Korean, but offline only, no word boosting, and known to invent text
  over silence; long audio is cut at quiet points into ≤ 60 s requests.
build.nvidia.com's nemotron-asr-streaming is English-only, so it is not an option for Korean interviews.

Which model answers is decided by the NVCF function id. The defaults below are the public hosted deployments;
NVIDIA changes an id when it redeploys a model, so STT_FUNCTION_ID_PARAKEET / STT_FUNCTION_ID_WHISPER override them.
`uv run python -m loop.cli stt-models` lists what the function actually serves (look for ko-KR).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import time
import wave
from array import array
from pathlib import Path

from . import store
from .llm import api_key, load_env

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "stt_cache"
RUNS_LOG = ROOT / "data" / "stt_runs.jsonl"
CHUNK_SECONDS = 0.2     # streaming chunk size
SEGMENT_SECONDS = 60    # offline requests stay well under gRPC's 4 MB message limit (60 s of 16 kHz PCM ≈ 1.9 MB)

ENGINES = {
    "parakeet": {"model": "nvidia/parakeet-1.1b-rnnt-multilingual-asr", "function_id": "71203149-d3b7-4460-8231-1be2543a1fca",
                 "language": "ko-KR", "mode": "streaming", "boost": True},
    "whisper": {"model": "openai/whisper-large-v3", "function_id": "b702f636-f60c-4a3d-a6f4-f3568c13bd7d",
                "language": "multi", "mode": "offline", "boost": False},   # «multi» = automatic language detection
}


class SttUnavailable(RuntimeError):
    pass


def settings(engine: str | None = None) -> dict:
    load_env()
    engine = (engine or os.environ.get("STT_ENGINE", "parakeet")).lower()
    if engine not in ENGINES:
        raise SttUnavailable(f"STT 엔진은 {', '.join(ENGINES)} 중 하나입니다: {engine!r}")
    e = ENGINES[engine]
    return {
        "engine": engine, "model": e["model"],
        "server": os.environ.get("STT_SERVER", "grpc.nvcf.nvidia.com:443"),
        "function_id": os.environ.get(f"STT_FUNCTION_ID_{engine.upper()}", e["function_id"]),
        "language": os.environ.get("STT_LANGUAGE", e["language"]),
        "mode": e["mode"],
        "boost": float(os.environ.get("STT_BOOST", "20")) if e["boost"] else 0.0,
    }


def keyterms(contract: dict) -> list[str]:
    """Words the recogniser should prefer — the contract's vocabulary, decided by people, not by the model."""
    terms = list(contract.get("stt_keyterms", [])) + [contract["drug_ko"], contract["drug"]]
    for seg in contract["segments"]:
        terms += [p for p in re.split(r"\s*·\s*", seg) if not re.search(r"\d", p)]   # «소아 10세 미만» boosts nothing useful
    return list(dict.fromkeys(t.strip() for t in terms if t.strip()))


def to_pcm_wav(src: Path) -> bytes:
    """16-bit PCM WAV bytes. A mono 16-bit WAV passes through; anything else (m4a from a phone, mp3, stereo)
    goes through ffmpeg to 16 kHz mono."""
    raw = src.read_bytes()
    try:
        with wave.open(io.BytesIO(raw)) as w:
            if w.getnchannels() == 1 and w.getsampwidth() == 2:
                return raw
    except (wave.Error, EOFError):
        pass
    if not shutil.which("ffmpeg"):
        raise SttUnavailable(f"{src.suffix or '이'} 형식은 변환이 필요한데 ffmpeg 이 없습니다 — "
                             "16-bit mono WAV 로 올리거나 ffmpeg 을 설치하세요.")
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(src),
                          "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-f", "wav", "pipe:1"],
                         capture_output=True, check=False)
    if out.returncode != 0 or not out.stdout:
        raise SttUnavailable(f"음성 변환 실패 — {out.stderr.decode(errors='replace')[:200]}")
    return out.stdout


def _pcm(wav_bytes: bytes) -> tuple[bytes, int, float]:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    return frames, rate, len(frames) / 2 / rate


def _log(**rec) -> None:
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_LOG.open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False) + "\n")


def transcribe(src: Path, contract: dict, force: bool = False, engine: str | None = None) -> dict:
    """Transcript record for one recording, from cache if this exact audio was transcribed before with the
    same model, language and vocabulary."""
    cfg = settings(engine)
    audio_sha = hashlib.sha256(src.read_bytes()).hexdigest()
    terms = keyterms(contract) if cfg["boost"] else []
    key = hashlib.sha256(json.dumps([audio_sha, cfg["model"], cfg["function_id"], cfg["language"], cfg["mode"],
                                     terms, cfg["boost"]], ensure_ascii=False).encode()).hexdigest()[:32]
    path = CACHE_DIR / f"{key}.json"
    t0 = time.time()
    if path.exists() and not force:
        rec = json.loads(path.read_text())
        _log(model=cfg["model"], key=key, cached=True, seconds=rec.get("duration_s"), ms=int((time.time() - t0) * 1000))
        return rec

    pcm, rate, seconds = _pcm(to_pcm_wav(src))
    text = words = None
    for attempt in range(4):   # the free endpoint rate-limits bursts
        try:
            text, words = _recognize(pcm, rate, terms, cfg)
            if not text.strip():
                raise ValueError("전사 결과가 비어 있습니다 — 무음이거나 지원하지 않는 언어일 수 있습니다")
            break
        except SttUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — UNAVAILABLE / RESOURCE_EXHAUSTED from gRPC, or an empty result
            if attempt == 3:
                raise
            _log(model=cfg["model"], key=key, retry=attempt + 1, reason=f"{type(e).__name__}: {str(e)[:120]}")
            time.sleep(5 * (attempt + 1))
    rec = {"engine": cfg["engine"], "model": cfg["model"], "function_id": cfg["function_id"], "language": cfg["language"], "mode": cfg["mode"],
           "audio_sha256": audio_sha, "duration_s": round(seconds, 2), "keyterms": terms, "boost": cfg["boost"],
           "cache_key": key, "created_at": store.now(), "text": text, "words": words}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    _log(model=cfg["model"], key=key, cached=False, seconds=rec["duration_s"], chars=len(text),
         ms=int((time.time() - t0) * 1000))
    return rec


def _service(cfg: dict):
    import riva.client  # lazy: replaying from cache needs no gRPC stack
    auth = riva.client.Auth(uri=cfg["server"], use_ssl=True, metadata_args=[
        ["function-id", cfg["function_id"]], ["authorization", f"Bearer {api_key()}"]],
        options=[("grpc.max_send_message_length", 16 << 20), ("grpc.max_receive_message_length", 16 << 20)])
    return riva.client, riva.client.ASRService(auth)


def _recognize(pcm: bytes, rate: int, terms: list[str], cfg: dict) -> tuple[str, list[dict]]:
    riva, asr = _service(cfg)
    config = riva.RecognitionConfig(
        encoding=riva.AudioEncoding.LINEAR_PCM, sample_rate_hertz=rate, audio_channel_count=1,
        language_code=cfg["language"], max_alternatives=1, enable_automatic_punctuation=True,
        enable_word_time_offsets=True, verbatim_transcripts=False,   # numbers as digits: «공복혈당 110», not «백십»
    )
    if terms:
        riva.add_word_boosting_to_config(config, terms, cfg["boost"])
    if cfg["mode"] == "offline":
        results = [r for seg in _segments(pcm, rate) for r in asr.offline_recognize(seg, config).results]
    else:
        step = int(rate * CHUNK_SECONDS) * 2
        chunks = (pcm[i:i + step] for i in range(0, len(pcm), step))
        results = [r for resp in asr.streaming_response_generator(
                       chunks, riva.StreamingRecognitionConfig(config=config, interim_results=False))
                   for r in resp.results if r.is_final]
    texts, words = [], []
    for r in results:
        if not r.alternatives:
            continue
        alt = r.alternatives[0]
        texts.append(alt.transcript.strip())
        words += [{"word": w.word, "start_ms": w.start_time, "end_ms": w.end_time,
                   "confidence": round(w.confidence, 3)} for w in alt.words]
    return " ".join(t for t in texts if t), words


def _segments(pcm: bytes, rate: int, max_s: int = SEGMENT_SECONDS, look_back_s: int = 5) -> list[bytes]:
    """Cut 16-bit mono PCM into pieces of at most `max_s` seconds, each cut placed at the quietest 100 ms in the
    last `look_back_s` seconds before the limit — so a cut lands between words, not inside one."""
    samples = array("h", pcm)
    win, limit, back = rate // 10, max_s * rate, look_back_s * rate
    out, start = [], 0
    while len(samples) - start > limit:
        lo, hi = start + limit - back, start + limit
        cut = min(range(lo, hi - win + 1, win), key=lambda i: sum(abs(x) for x in samples[i:i + win]))
        out.append(samples[start:cut].tobytes())
        start = cut
    out.append(samples[start:].tobytes())
    return out


def list_models(engine: str | None = None) -> dict[str, list[str]]:
    """language code → model names the configured function serves. Use it to confirm ko-KR before recording."""
    cfg = settings(engine)
    riva, asr = _service(cfg)
    resp = asr.stub.GetRivaSpeechRecognitionConfig(riva.proto.riva_asr_pb2.RivaSpeechRecognitionConfigRequest(),
                                                  metadata=asr.auth.get_auth_metadata())
    out: dict[str, list[str]] = {}
    for m in resp.model_config:
        out.setdefault(m.parameters.get("language_code", "?"), []).append(
            f'{m.model_name} ({m.parameters.get("type", "?")})')
    return dict(sorted(out.items()))


def add_note(rec: dict, *, hcp_ref: str, specialty: str, date: str, consent_by: str, audio_name: str,
             notes_path: Path | None = None) -> dict:
    """Append the transcript as a field note. Consent is a named person, like the two gates downstream —
    no name, no note. The same recording is never added twice."""
    notes_path = notes_path or store.FIELD_NOTES
    if not consent_by.strip():
        raise SystemExit("녹음 동의를 확인한 사람의 이름이 없습니다 — 동의 확인 없이는 면담 기록이 되지 않습니다.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise SystemExit(f"면담 날짜 형식이 아닙니다: {date!r} (YYYY-MM-DD)")
    notes = json.loads(notes_path.read_text()) if notes_path.exists() else []
    for n in notes:
        if n.get("stt", {}).get("audio_sha256") == rec["audio_sha256"]:
            raise SystemExit(f"이미 전사해 넣은 음성입니다 — {n['doc_id']}")
    prefix = f"FN-{date[:4]}-{date[5:7]}{date[8:]}-"
    seq = max([int(n["doc_id"][len(prefix):]) for n in notes if n["doc_id"].startswith(prefix)] + [0]) + 1
    note = {"doc_id": f"{prefix}{seq:02d}", "hcp_ref": hcp_ref.strip(), "specialty": specialty.strip(), "date": date,
            "synthetic": False, "text": rec["text"],
            "stt": {"engine": rec.get("engine"), "model": rec["model"], "language": rec["language"], "audio_name": audio_name,
                    "audio_sha256": rec["audio_sha256"], "duration_s": rec["duration_s"], "cache_key": rec["cache_key"],
                    "consent_by": consent_by.strip(), "transcribed_at": rec["created_at"]}}
    # append in place — the file is hand-formatted, one note per entry; re-dumping it would rewrite every line
    body = notes_path.read_text().rstrip() if notes else "[\n]"
    entry = "  " + json.dumps(note, ensure_ascii=False)
    body = body[:-1].rstrip() + ("," if notes else "") + "\n" + entry + "\n]\n"
    json.loads(body)   # never leave a broken input file behind
    notes_path.write_text(body)
    return note
