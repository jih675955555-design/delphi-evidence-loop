"""The single wrapper every text-to-speech call goes through — resembleai/chatterbox-multilingual-tts on build.nvidia.com.

It exists for one scene: the collection demo (loop/collect.py). A synthetic interview script is read aloud so a
person can listen to it, and the same audio file is what the STT model hears. Nothing downstream reads this
module's output except STT — the loop's evidence is still the transcript.

Same rules as llm.py / stt.py:
1. Every synthesis is cached on disk by a hash of the text and every setting that changes the sound — same text,
   same audio, no second API call — and logged to data/tts_runs.jsonl (model, chars, chunks, seconds, cache hit).
   Caching is not an optimisation here: Chatterbox is non-deterministic (the same sentence gave 7.57 s and 7.69 s,
   different bytes), so a second call would give the judge and the STT different audio.
2. The key never leaves the host: NVIDIA_API_KEY from .env, never written to logs or cache.
3. Empty audio is an error, never a cached result.

Riva gRPC on NVCF (grpc.nvcf.nvidia.com:443), SpeechSynthesisService.Synthesize. Measured 2026-09-28:
- One Korean voice, Chatterbox-Multilingual.ko-KR.Male, 24 kHz 16-bit mono PCM. 23 languages in all.
- The function reports max_input_length = 500 characters, but that is not the binding limit: one request stops at
  500 speech tokens (~20 s of audio). A 220-character script failed as one request with «Audio generation was
  truncated». So text is cut at sentence boundaries into pieces of at most MAX_CHUNK_CHARS (≤ 12.4 s of audio each,
  measured), synthesised 2 at a time, and joined with short silences. On a truncation error the piece is split in
  half and retried.
- LEAD_S of silence before the first word, so the player and the STT stream both start on silence. It does not
  rescue a weak first word: Parakeet dropped a script's opening «당뇨로 진단된» with 0.4, 1 and 2 s of lead alike
  (Whisper heard it), so the script was rewritten to open with «열다섯 살 청소년이 …».
- Synthesis runs at 2.2–2.5× real time (a 25 s script in about 10 s).

Writing text that survives TTS → STT (measured on the round trip):
- no Latin letters — «PCOS» was read «피고», «eGFR» «에게펄»;
- numbers in Korean reading — «1,000mg» came back «천억», «천 밀리그램» round-trips;
- avoid words the voice slurs — «콩팥» came back «콤바», «제2형» «대도형»; «신장» is safe;
- keep a key word out of the very first position of a script (see LEAD_S above).

The function id is the public hosted deployment; TTS_FUNCTION_ID overrides it when NVIDIA redeploys.
`uv run python -m loop.cli tts-voices` lists what the function serves.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import store
from .llm import api_key, env

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "tts_cache"
RUNS_LOG = ROOT / "data" / "tts_runs.jsonl"
ENGINE = {"model": "resembleai/chatterbox-multilingual-tts", "function_id": "ddacc747-1269-4fab-bfd9-8f593dead106",
          "voice": "Chatterbox-Multilingual.ko-KR.Male", "language": "ko-KR", "sample_rate": 24000}
MAX_CHUNK_CHARS = 110   # ≤ 12.4 s of speech per request, well under the ~20 s cap
LEAD_S, GAP_S, TAIL_S = 0.4, 0.25, 0.3
MAX_SPLIT_DEPTH = 2
TIMEOUT_S = 60          # one piece takes about 5 s; a request still open after this is cut off (DEADLINE) and retried


class TtsUnavailable(RuntimeError):
    pass


def settings() -> dict:
    return {
        "model": ENGINE["model"],
        "server": env("TTS_SERVER", "grpc.nvcf.nvidia.com:443"),
        "function_id": env("TTS_FUNCTION_ID", ENGINE["function_id"]),
        "voice": env("TTS_VOICE", ENGINE["voice"]),
        "language": env("TTS_LANGUAGE", ENGINE["language"]),
        "sample_rate": ENGINE["sample_rate"],
    }


def _split_long(piece: str, limit: int) -> list[str]:
    """A sentence longer than `limit`: cut at commas first, then at spaces. Only a single word longer than the
    limit is cut mid-word — no Korean sentence in a script has one."""
    for sep in (r"(?<=,)\s+", r"\s+"):
        parts = [p for p in re.split(sep, piece) if p]
        if len(parts) > 1:
            return _pack(parts, limit, lambda p: _split_long(p, limit))
    return [piece[i:i + limit] for i in range(0, len(piece), limit)]


def _pack(parts: list[str], limit: int, too_long) -> list[str]:
    out, cur = [], ""
    for p in parts:
        if len(p) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out += too_long(p)
        elif cur and len(cur) + 1 + len(p) > limit:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}" if cur else p
    if cur:
        out.append(cur)
    return out


def chunks(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Pieces of at most `limit` characters, cut after . ? ! and packed greedily. Lossless:
    ' '.join(chunks(t)) == ' '.join(t.split())."""
    norm = " ".join(text.split())
    if not norm:
        return []
    sentences = [s for s in re.split(r"(?<=[.?!])\s+", norm) if s]
    return _pack(sentences, limit, lambda s: _split_long(s, limit))


def _cache_key(cfg: dict, text: str) -> str:
    return hashlib.sha256(json.dumps([cfg["model"], cfg["function_id"], cfg["voice"], cfg["language"], cfg["sample_rate"],
                                      MAX_CHUNK_CHARS, LEAD_S, GAP_S, TAIL_S, text], ensure_ascii=False).encode()).hexdigest()[:32]


def _log(**rec) -> None:
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUNS_LOG.open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False) + "\n")


def _silence(seconds: float, rate: int) -> bytes:
    return b"\x00\x00" * int(round(seconds * rate))


def synthesize(text: str, force: bool = False) -> dict:
    """24 kHz 16-bit mono WAV for `text`, from cache if this exact text was synthesised before with the same
    voice and chunking. Returns the record (path, hashes, duration, chunk count)."""
    cfg = settings()
    text = " ".join(text.split())
    if not text:
        raise TtsUnavailable("읽을 글이 비어 있습니다.")
    key = _cache_key(cfg, text)
    wav_path, meta_path = CACHE_DIR / f"{key}.wav", CACHE_DIR / f"{key}.json"
    t0 = time.time()
    if wav_path.exists() and meta_path.exists() and not force:
        rec = json.loads(meta_path.read_text())
        _log(model=cfg["model"], key=key, cached=True, seconds=rec.get("duration_s"), ms=int((time.time() - t0) * 1000))
        return {**rec, "path": str(wav_path)}

    pieces = chunks(text)
    rate = cfg["sample_rate"]
    with ThreadPoolExecutor(max_workers=2) as pool:      # the free endpoint rate-limits bursts; 2 is enough for ≤ 40 s
        pcms = list(pool.map(lambda p: _synth_piece(p, cfg, key), pieces))   # map keeps the order
    pcm = _silence(LEAD_S, rate) + _silence(GAP_S, rate).join(pcms) + _silence(TAIL_S, rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    data = buf.getvalue()
    rec = {"cache_key": key, "model": cfg["model"], "function_id": cfg["function_id"], "voice": cfg["voice"],
           "language": cfg["language"], "sample_rate": rate, "chunks": len(pieces), "chunk_chars": [len(p) for p in pieces],
           "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "wav_sha256": hashlib.sha256(data).hexdigest(),
           "duration_s": round(len(pcm) / 2 / rate, 2), "created_at": store.now()}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = wav_path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(wav_path)
    meta_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    _log(model=cfg["model"], key=key, cached=False, chars=len(text), chunks=len(pieces), seconds=rec["duration_s"],
         ms=int((time.time() - t0) * 1000))
    return {**rec, "path": str(wav_path)}


def _halves(piece: str) -> tuple[str, str]:
    """Cut near the middle, at the space closest to it."""
    mid = len(piece) // 2
    spaces = [m.start() for m in re.finditer(r"\s", piece)]
    cut = min(spaces, key=lambda i: abs(i - mid)) if spaces else mid
    return piece[:cut].strip(), piece[cut:].strip()


def _synth_piece(piece: str, cfg: dict, key: str, depth: int = 0) -> bytes:
    """One chunk with retries. A truncation (the ~20 s cap) splits the chunk in half; transient errors back off."""
    for attempt in range(4):
        try:
            pcm = _synth_chunk(piece, cfg)
            if not pcm:
                raise TtsUnavailable(f"합성 결과가 비어 있습니다 — «{piece[:30]}…»")
            return pcm
        except TtsUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — gRPC errors carry the reason in str(e)
            msg = str(e)
            if "truncated" in msg.lower() and depth < MAX_SPLIT_DEPTH and len(piece.split()) > 1:
                _log(model=cfg["model"], key=key, split=depth + 1, chars=len(piece))
                a, b = _halves(piece)
                return _synth_piece(a, cfg, key, depth + 1) + _silence(GAP_S, cfg["sample_rate"]) + _synth_piece(b, cfg, key, depth + 1)
            transient = any(code in msg for code in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE"))
            if attempt == 3 or not transient:
                raise
            _log(model=cfg["model"], key=key, retry=attempt + 1, reason=f"{type(e).__name__}: {msg[:120]}")
            time.sleep(4 * (attempt + 1))
    raise AssertionError("unreachable")


def _service(cfg: dict):
    import riva.client  # lazy: replaying from cache needs no gRPC stack
    auth = riva.client.Auth(uri=cfg["server"], use_ssl=True, metadata_args=[
        ["function-id", cfg["function_id"]], ["authorization", f"Bearer {api_key()}"]])
    return riva.client, riva.client.SpeechSynthesisService(auth)


def _synth_chunk(text: str, cfg: dict) -> bytes:
    """Raw 16-bit mono PCM for one short piece — the only function here that touches the network."""
    import grpc
    _, tts = _service(cfg)
    # riva's synthesize takes no deadline; its future does — without one a stalled request would hold the page forever
    fut = tts.synthesize(text, voice_name=cfg["voice"], language_code=cfg["language"], sample_rate_hz=cfg["sample_rate"], future=True)
    try:
        return fut.result(timeout=TIMEOUT_S).audio
    except grpc.FutureTimeoutError:
        fut.cancel()
        raise RuntimeError(f"DEADLINE — TTS 응답이 {TIMEOUT_S}초 안에 오지 않았습니다") from None


def list_voices() -> dict[str, list[str]]:
    """language code → voice names the configured function serves."""
    cfg = settings()
    riva, tts = _service(cfg)
    resp = tts.stub.GetRivaSynthesisConfig(riva.proto.riva_tts_pb2.RivaSynthesisConfigRequest(),
                                           metadata=tts.auth.get_auth_metadata())
    out: dict[str, list[str]] = {}
    for m in resp.model_config:
        p = m.parameters
        langs = [x.strip() for x in p.get("language_code", "?").split(",") if x.strip()]
        voices = [v.strip() for v in re.split(r"[,\s]+", p.get("subvoices", "")) if v.strip()]
        base = p.get("voice_name", m.model_name)
        for lang in langs:
            names = [f"{base}.{v.split(':')[0]}" for v in voices if v.split(":")[0].startswith(lang)] or [base]
            out.setdefault(lang, [])
            out[lang] += [n for n in names if n not in out[lang]]
    return dict(sorted(out.items()))
