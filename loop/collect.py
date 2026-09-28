"""Collection — the stage before `sense`: a written interview script is read aloud (TTS), a person listens while
the STT model listens to the same file, and what STT wrote down becomes one field note under a named consent.

The scripts live in the repository (data/field_scripts/FS-NN.json, one file each) — on GitHub they are the files
the collection page links to. They are synthetic: fictional HCPs, no real people or institutions. A script's
`intent` (which segment × signal it was written to add to) is the author's design note; it is shown to the
reader and never enters the note or the model's input.

What «the judge heard = what STT heard» rests on: one MP3 per script. The browser plays exactly that file and STT
decodes exactly that file; its sha256 is on the page and in the note's provenance (note["stt"]).

Two storage layers:
- baked (committed, written only by scripts/collect_smoke.py --bake): data/field_scripts/audio/{sid}.mp3 + .json,
  data/field_scripts/stt/{cache_key}.json, data/field_scripts/bake.json. With these the whole chain replays
  without a key — audio, the transcript shown in step with the audio, and (from data/llm_cache) the extraction.
- runtime (gitignored): data/collect_runtime/{audio,live}/ — re-synthesised audio and the live listening record
  the page polls. Demo clicks never touch committed files.

Guards: a script is collected once (script_id), a recording once (audio hash, in stt.add_note), and the note text
is exactly the transcript the listener saw. «수집 되돌리기» removes only what collection added, and refuses once a
person has signed anything derived from it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path

from . import sense, store, stt, tts
from .llm import load_env

SCRIPTS_DIR = store.DATA / "field_scripts"      # baked: audio/, stt/, bake.json live under it
RUNTIME_DIR = store.DATA / "collect_runtime"    # audio/, live/ — gitignored
SCRIPT_ID = re.compile(r"FS-\d{2,3}")
MAX_TEXT = 600
REQUIRED = ("title_ko", "hcp_ref", "specialty", "date", "text")
MP3 = ["-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", "48k", "-ac", "1", "-ar", "24000",
       "-fflags", "+bitexact", "-flags:a", "+bitexact"]   # 48 kbps mono: ~150 KB for 25 s, plays in every browser


class ListenRefused(Exception):
    """A listen that cannot start — the web answers 409 with the code and the Korean reason."""
    def __init__(self, code: str, message_ko: str):
        super().__init__(message_ko)
        self.code, self.message_ko = code, message_ko


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ── scripts ───────────────────────────────────────────────────────────────────

def _invalid(s, sid: str, contract: dict) -> str | None:
    if not isinstance(s, dict):
        return "대본은 JSON 객체여야 합니다"
    if not SCRIPT_ID.fullmatch(sid):
        return "파일 이름이 대본 번호 형식(FS-01)이 아닙니다"
    if s.get("script_id") != sid:
        return f"script_id({s.get('script_id')!r})가 파일 이름({sid})과 다릅니다"
    if s.get("synthetic") is not True:
        return "synthetic: true 가 아닙니다 — 이 화면은 합성 대본만 다룹니다"
    for k in REQUIRED:
        if not isinstance(s.get(k), str) or not s[k].strip():
            return f"{k} 가 비어 있습니다"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["date"]):
        return f"date 형식이 아닙니다: {s['date']!r} (YYYY-MM-DD)"
    if len(s["text"]) > MAX_TEXT:
        return f"text 가 {MAX_TEXT}자를 넘습니다 ({len(s['text'])}자)"
    it = s.get("intent")
    if not isinstance(it, dict):
        return "intent 가 없습니다"
    if it.get("segment") not in contract["segments"]:
        return f"intent.segment 가 계약의 환자군이 아닙니다: {it.get('segment')!r}"
    if it.get("signal_type") not in contract["signal_types"]:
        return f"intent.signal_type 이 계약의 신호 유형이 아닙니다: {it.get('signal_type')!r}"
    if not isinstance(it.get("why_ko"), str):
        return "intent.why_ko 가 없습니다"
    return None


def _load(path: Path, contract: dict) -> dict:
    sid = path.stem
    try:
        raw = path.read_bytes()
        s = json.loads(raw)
    except (OSError, ValueError) as e:
        return {"script_id": sid, "_error": f"JSON 을 읽지 못했습니다 — {str(e)[:120]}"}
    err = _invalid(s, sid, contract)
    if err:
        return {"script_id": sid, "title_ko": s.get("title_ko", "") if isinstance(s, dict) else "", "_error": err}
    out = {**s, "chars": len(s["text"]), "text_sha256": _sha(s["text"].encode()), "file_sha256": _sha(raw)}
    if re.search(r"[A-Za-z]", s["text"]):   # the ko-KR voice reads Latin letters badly (PCOS → «피고»)
        out["_warn"] = "라틴 문자가 있어 TTS가 잘못 읽을 수 있다"
    return out


def list_scripts(contract: dict | None = None) -> list[dict]:
    """Every data/field_scripts/FS-*.json, validated. A broken file is listed with `_error`, not raised."""
    contract = contract or store.contract()
    return [_load(p, contract) for p in sorted(SCRIPTS_DIR.glob("FS-*.json"))]


def get(sid: str, contract: dict | None = None) -> dict:
    if not isinstance(sid, str) or not SCRIPT_ID.fullmatch(sid):
        raise SystemExit(f"대본 번호 형식이 아닙니다: {str(sid)[:40]!r} (예: FS-01)")
    path = SCRIPTS_DIR / f"{sid}.json"
    if not path.exists():
        raise SystemExit(f"대본이 없습니다: {sid}")
    s = _load(path, contract or store.contract())
    if "_error" in s:
        raise SystemExit(f"{sid} 대본이 형식에 맞지 않습니다 — {s['_error']}")
    return s


# ── where the scripts come from ───────────────────────────────────────────────

_SOURCE: dict | None = None
_REMOTE = (re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/*$"),
           re.compile(r"/git/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/*$"))


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(store.ROOT), *args], capture_output=True, text=True, timeout=2, check=False)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def source_info() -> dict:
    """{repo: owner/name, ref, commit, path} of the checkout the scripts are read from. Only owner/name is taken
    from a remote URL — a remote can carry a token, and nothing else of it is ever shown."""
    global _SOURCE
    if _SOURCE is None:
        env = os.environ
        repo = env.get("COLLECT_GITHUB_REPO", "").strip()
        if not repo and env.get("RAILWAY_GIT_REPO_OWNER") and env.get("RAILWAY_GIT_REPO_NAME"):
            repo = f'{env["RAILWAY_GIT_REPO_OWNER"]}/{env["RAILWAY_GIT_REPO_NAME"]}'
        ref = env.get("COLLECT_GITHUB_REF", "").strip() or env.get("RAILWAY_GIT_BRANCH", "").strip()
        commit = env.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
        if not repo:
            url = _git("remote", "get-url", "origin")
            m = next((m for m in (r.search(url) for r in _REMOTE) if m), None)
            repo = f"{m[1]}/{m[2]}" if m else ""
        if not ref:
            ref = _git("rev-parse", "--abbrev-ref", "HEAD")
            ref = "" if ref == "HEAD" else ref   # detached checkout: fall back to the commit
        commit = commit or _git("rev-parse", "HEAD")
        _SOURCE = {"repo": repo if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) else "",
                   "ref": ref if re.fullmatch(r"[A-Za-z0-9_./-]{1,120}", ref) else "",
                   "commit": commit if re.fullmatch(r"[0-9a-f]{7,40}", commit) else "",
                   "path": "data/field_scripts"}
    return _SOURCE


def blob_url(sid: str) -> str | None:
    src = source_info()
    ref = src["ref"] or src["commit"]
    return f"https://github.com/{src['repo']}/blob/{ref}/{src['path']}/{sid}.json" if src["repo"] and ref else None


def can_call_models() -> bool:
    """Whether a key is configured — never the key itself."""
    load_env()
    return bool(os.environ.get("NVIDIA_API_KEY", "").strip())


def stt_problem(engine: str = "parakeet") -> str | None:
    """Why the STT settings cannot be used (a bad STT_BOOST in .env, say), or None. The pages show the reason and
    leave the listen buttons out instead of failing — every transcript lookup needs the settings for its cache key."""
    try:
        stt.settings(engine)
    except stt.SttUnavailable as e:
        return str(e)
    return None


# ── audio ─────────────────────────────────────────────────────────────────────

def audio(script: dict) -> dict | None:
    """The MP3 for this script's current text — runtime (re-synthesised) first, then baked. A file whose sidecar
    was made from different text, or whose bytes do not match the sidecar hash, is not used."""
    sid = script["script_id"]
    for source, d in (("runtime", RUNTIME_DIR / "audio"), ("baked", SCRIPTS_DIR / "audio")):
        meta, mp3 = d / f"{sid}.json", d / f"{sid}.mp3"
        if not (meta.exists() and mp3.exists()):
            continue
        try:
            m = json.loads(meta.read_text())
        except ValueError:
            continue
        if m.get("text_sha256") != script["text_sha256"] or _sha(mp3.read_bytes()) != m.get("audio_sha256"):
            continue
        return {**m, "path": str(mp3), "source": source}
    return None


def make_audio(script: dict, *, baked: bool = False, force: bool = False) -> dict:
    """Chatterbox TTS → WAV (cached in data/tts_cache) → the MP3 both the listener and STT get."""
    if not shutil.which("ffmpeg"):
        raise SystemExit("음성을 MP3 로 바꾸려면 ffmpeg 이 필요합니다 — 설치한 뒤 다시 실행한다.")
    sid = script["script_id"]
    t = tts.synthesize(script["text"], force=force)
    d = (SCRIPTS_DIR if baked else RUNTIME_DIR) / "audio"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{sid}.tmp.mp3"
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", t["path"], *MP3, str(tmp)],
                         capture_output=True, check=False)
    if out.returncode != 0 or not tmp.exists() or not tmp.stat().st_size:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"MP3 변환 실패 — {out.stderr.decode(errors='replace')[:200]}")
    data = tmp.read_bytes()
    tmp.replace(d / f"{sid}.mp3")
    meta = {"script_id": sid, "text_sha256": script["text_sha256"], "file": f"{sid}.mp3", "audio_sha256": _sha(data),
            "bytes": len(data), "duration_s": t["duration_s"], "format": "mp3 · 48 kbps · mono · 24 kHz", "created_at": store.now(),
            "tts": {k: t[k] for k in ("model", "function_id", "voice", "language", "sample_rate", "chunks", "chunk_chars",
                                      "cache_key", "wav_sha256", "created_at")}}
    (d / f"{sid}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
    return {**meta, "path": str(d / f"{sid}.mp3"), "source": "baked" if baked else "runtime"}


# ── transcripts ───────────────────────────────────────────────────────────────

def stt_key(a: dict, engine: str = "parakeet") -> str:
    cfg = stt.settings(engine)
    return stt.cache_key(a["audio_sha256"], cfg, stt.keyterms(store.contract()) if cfg["boost"] else [])


def saved_transcript(script: dict, engine: str = "parakeet") -> dict | None:
    """A transcript of the current audio that needs no model call — baked first, then the local STT cache."""
    a = audio(script)
    if not a:
        return None
    key = stt_key(a, engine)
    for d in (SCRIPTS_DIR / "stt", stt.CACHE_DIR):
        p = d / f"{key}.json"
        if p.exists():
            rec = json.loads(p.read_text())
            if rec.get("audio_sha256") == a["audio_sha256"]:
                return rec
    return None


def transcript(script: dict, *, engine: str = "parakeet", mode: str = "cached", on_partial=None, pace: float = 0.0) -> dict:
    """STT record for the script's audio. replay: saved only · live: the model listens again · cached: saved, else the model."""
    a = audio(script)
    if not a:
        raise SystemExit(f"{script['script_id']} 음성이 아직 없습니다 — 먼저 음성을 만든다 (Chatterbox TTS).")
    if mode in ("replay", "cached"):
        rec = saved_transcript(script, engine)
        if rec:
            return rec
        if mode == "replay":
            raise SystemExit("저장된 전사가 없습니다 — 키가 있으면 STT가 실시간으로 듣는다.")
    return stt.transcribe(Path(a["path"]), store.contract(), force=mode == "live", engine=engine,
                          on_partial=on_partial, pace=pace)


# ── listening (the page polls the live file) ──────────────────────────────────

_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}


def _live_path(sid: str) -> Path:
    return RUNTIME_DIR / "live" / f"{sid}.json"


def _write_live(sid: str, rec: dict) -> None:
    p = _live_path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False))
    tmp.replace(p)   # atomic on POSIX — a poll never reads half a file


def busy() -> str | None:
    """The script being listened to in this process right now, if any."""
    return next((sid for sid, t in list(_THREADS.items()) if t.is_alive()), None)


def live_read(sid: str) -> dict | None:
    p = _live_path(sid)
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text())
    except ValueError:
        return None
    t = _THREADS.get(sid)
    if rec.get("status") == "RUNNING" and not (t and t.is_alive()):
        # written by a thread that is gone (server restarted) — say so, and let the button start again
        rec = {**rec, "status": "ERROR", "error": "듣기가 중간에 끊겼습니다 (서버 재시작) — 다시 누르면 처음부터 듣는다."}
    return rec


def _sentences(shown: str) -> tuple[list[str], str]:
    """Split revealed text into finished sentences (ink) and the sentence still being heard (grey)."""
    m = list(re.finditer(r"[.?!](?=\s|$)", shown))
    if not m:
        return [], shown.strip()
    cut = m[-1].end()
    return ([shown[:cut].strip()] if shown[:cut].strip() else []), shown[cut:].strip()


def _reveal(rec: dict, push, pace: float, duration_s: float) -> None:
    """Replay a saved transcript in step with the audio: each word appears when it has been said (its end time)."""
    text, t0 = rec["text"], time.time()
    marks, pos = [], 0
    for w in rec.get("words") or []:
        j = text.find(w["word"], pos) if w.get("word") else -1
        if j >= 0:
            pos = j + len(w["word"])
            marks.append((w.get("end_ms") or w.get("start_ms") or 0, pos))
    if not marks:   # no word times: spread the text over the audio
        marks = [(int(duration_s * 1000 * k / 20), int(len(text) * k / 20)) for k in range(1, 21)]
    for at_ms, end in marks:
        if pace > 0:
            wait = t0 + at_ms / 1000 / pace - time.time()
            if wait > 0:
                time.sleep(wait)
        push(*_sentences(text[:end]))
    if pace > 0:   # done when the audio is, not when the last word is — the page reloads on DONE
        wait = t0 + duration_s / pace - time.time()
        if wait > 0:
            time.sleep(wait)


def _run(script: dict, a: dict, live: dict, rec: dict | None, engine: str, pace: float) -> None:
    sid, t0, last = script["script_id"], time.time(), {"at": 0.0, "n": 0}

    def push(finals: list[str], interim: str) -> None:
        live.update(finals=finals, interim=interim, elapsed_s=round(time.time() - t0, 2), updated_at=store.now())
        if time.time() - last["at"] >= 0.2 or len(finals) != last["n"]:   # throttle, but never hold back a final
            last["at"], last["n"] = time.time(), len(finals)
            _write_live(sid, live)
    try:
        if live["mode"] == "replay":
            _reveal(rec, push, pace, a["duration_s"])
        else:
            rec = transcript(script, engine=engine, mode="live", on_partial=push, pace=pace)
        if rec.get("audio_sha256") != a["audio_sha256"]:
            raise SystemExit("전사한 음성과 재생한 음성의 해시가 다릅니다.")
        live.update(status="DONE", finals=[rec["text"]], interim="", text=rec["text"], cer=stt.cer(rec["text"], script["text"]),
                    rec=rec, elapsed_s=round(time.time() - t0, 2), updated_at=store.now())
    except SystemExit as e:
        live.update(status="ERROR", error=str(e), updated_at=store.now())
    except Exception as e:  # noqa: BLE001 — shown on the page, never swallowed silently
        traceback.print_exc()
        live.update(status="ERROR", error=f"{type(e).__name__}: {str(e)[:300]}", updated_at=store.now())
    _write_live(sid, live)


def listen(sid: str, *, mode: str = "live", engine: str = "parakeet", pace: float = 1.0, background: bool = True) -> dict:
    """Start listening to one script's audio. live: Parakeet streams the same MP3 at `pace`× real time and the
    interim text goes to the live file. replay: the saved transcript is revealed by its word times — no model call.
    One listen at a time per process."""
    script = get(sid)
    if mode not in ("live", "replay"):
        raise ListenRefused("BAD_MODE", f"듣기 방식은 live 또는 replay 입니다: {mode!r}")
    problem = stt_problem(engine)
    if problem:
        raise ListenRefused("STT_CONFIG", problem)
    with _LOCK:
        other = busy()
        if other:
            raise ListenRefused("BUSY", f"지금 {other} 을(를) 듣는 중입니다 — 끝난 뒤에 다시 누르세요.")
        a = audio(script)
        if not a:
            raise ListenRefused("NO_AUDIO", "이 대본의 음성이 아직 없습니다 — 먼저 음성을 만듭니다.")
        rec = None
        if mode == "replay":
            rec = saved_transcript(script, engine)
            if not rec:
                raise ListenRefused("NO_TRANSCRIPT", "저장된 전사가 없습니다 — 키가 있으면 STT가 실시간으로 듣습니다.")
        elif not can_call_models():
            raise ListenRefused("NO_KEY", "실시간으로 들으려면 NVIDIA_API_KEY 가 필요합니다 — 저장된 전사로 재생할 수 있습니다.")
        cfg = stt.settings(engine)
        live = {"script_id": sid, "status": "RUNNING", "mode": mode, "engine": cfg["engine"], "model": cfg["model"],
                "boost": cfg["boost"], "started_at": store.now(), "updated_at": store.now(), "audio_sha256": a["audio_sha256"],
                "duration_s": a["duration_s"], "finals": [], "interim": "", "elapsed_s": 0.0, "text": "", "cer": None,
                "rec": None, "error": None}
        _write_live(sid, live)
        if background:
            t = threading.Thread(target=_run, args=(script, a, live, rec, engine, pace), daemon=True, name=f"listen-{sid}")
            _THREADS[sid] = t
            t.start()
            return dict(live)
    _run(script, a, live, rec, engine, pace)
    return dict(live)


# ── the note ──────────────────────────────────────────────────────────────────

def collected(notes: list[dict]) -> dict[str, dict]:
    """script_id → the field note it became."""
    return {n["stt"]["script_id"]: n for n in notes if (n.get("stt") or {}).get("script_id")}


def save(script: dict, consent_by: str, rec: dict, *, listen_mode: str) -> dict:
    """The transcript the listener saw → one field note. HCP, specialty and date come from the script (they keep
    the distinct-HCP count meaningful); the script's intent never enters the note."""
    sid = script["script_id"]
    notes = json.loads(store.FIELD_NOTES.read_text()) if store.FIELD_NOTES.exists() else []
    done = collected(notes).get(sid)
    if done:
        raise SystemExit(f"이미 수집한 대본입니다 — {done['doc_id']}")
    a = audio(script)
    if not a or rec.get("audio_sha256") != a["audio_sha256"]:
        raise SystemExit("STT가 들은 음성이 지금 이 대본의 음성과 다릅니다 — 다시 재생하며 받아 적어 주세요.")
    if not (rec.get("text") or "").strip():
        raise SystemExit("STT가 받아 적은 글이 비어 있습니다.")
    prov = {"source": "script", "script_id": sid, "script_sha256": script["file_sha256"], "text_sha256": script["text_sha256"],
            "listen_mode": listen_mode, "cer": round(stt.cer(rec["text"], script["text"]), 4),
            "tts": {k: a["tts"].get(k) for k in ("model", "voice", "function_id", "created_at")}}
    return stt.add_note(rec, hcp_ref=script["hcp_ref"], specialty=script["specialty"], date=script["date"],
                        consent_by=consent_by, audio_name=f"{sid}.mp3", synthetic=True, provenance=prov)


def effect(state: dict, contract: dict, notes: list[dict]) -> dict:
    """What collection changed downstream, computed by code: tally without the collected notes' claims vs with them."""
    docs = {n["doc_id"] for n in notes if (n.get("stt") or {}).get("script_id")}
    mm, mh = contract["threshold"]["min_mentions"], contract["threshold"]["min_hcps"]
    before = {(r["segment"], r["signal_type"]): r for r in sense.tally({"claims": [c for c in state["claims"] if c["doc_id"] not in docs]})}
    after = sense.tally(state)
    added = [c for c in state["claims"] if c["doc_id"] in docs]
    added_ids = {c["id"] for c in added}
    touched = {(c["segment"], c["signal_type"]) for c in added if c["verified"] and c["segment"] != "OTHER"}
    new_hyps = [h["id"] for h in state["hypotheses"] if set(h["field"]["claim_ids"]) & added_ids]
    hyp_by = {(h["segment"], h["signal_type"]): h["id"] for h in state["hypotheses"]}
    rows = []
    for r in after:
        k = (r["segment"], r["signal_type"])
        if k not in touched:
            continue
        b = before.get(k)
        bm, bh = (b["mentions"], b["hcps"]) if b else (0, 0)
        rows.append({"segment": k[0], "signal_type": k[1], "before": [bm, bh], "after": [r["mentions"], r["hcps"]],
                     "passed_before": bm >= mm and bh >= mh, "passed_after": r["mentions"] >= mm and r["hcps"] >= mh,
                     "need_mentions": max(0, mm - r["mentions"]), "need_hcps": max(0, mh - r["hcps"]),
                     "hypothesis_id": hyp_by.get(k), "new_hypothesis": hyp_by.get(k) in new_hyps})
    extracted = {c["doc_id"] for c in state["claims"]} | {c["doc_id"] for c in state["safety_queue"]}
    safety = [c["id"] for c in state["safety_queue"] if c["doc_id"] in docs]
    return {"docs": sorted(docs), "pending_docs": sorted(docs - extracted), "claims_added": len(added),
            "dropped": sum(1 for c in added if not c["verified"]), "rows": rows, "new_hypotheses": new_hyps,
            "safety_added": len(safety), "safety_ids": safety, "threshold": [mm, mh]}


def uncollect() -> dict:
    """Undo collection for the next viewer: the script-collected notes and only what was derived from them.
    Refuses once a person has signed a derived hypothesis — a convenience button never deletes a signature."""
    state = store.load()
    notes = json.loads(store.FIELD_NOTES.read_text())
    docs = {n["doc_id"] for n in notes if (n.get("stt") or {}).get("script_id")}
    if not docs:
        raise SystemExit("되돌릴 수집이 없습니다 — 대본에서 수집한 면담 기록이 없습니다.")
    ids = {c["id"] for c in state["claims"] + state["safety_queue"] if c["doc_id"] in docs}
    derived = [h for h in state["hypotheses"] if set(h["field"]["claim_ids"]) & ids]
    signed = [h["id"] for h in derived if h["status"] not in ("DRAFT", "SCREENED") or h["id"] in state["reviews"] or h["id"] in state["board"]]
    if signed:
        raise SystemExit(f"{', '.join(signed)} 에 사람의 서명이 있어 되돌리지 않습니다 — 서명된 기록은 지우지 않습니다.")
    gone = {h["id"] for h in derived}
    tail = [h["id"] for h in state["hypotheses"][len(state["hypotheses"]) - len(gone):]] if gone else []
    if set(tail) != gone:
        # ids are sequential (count + 1): removing one from the middle would let the next draft reuse a live id
        raise SystemExit("수집 뒤에 다른 가설이 더 생겨 번호가 꼬입니다 — 결과 전체 초기화(개요 · 초기화)를 쓰세요.")
    removed = stt.remove_notes(docs)
    n_claims = sum(1 for c in state["claims"] if c["doc_id"] in docs)
    n_safety = sum(1 for c in state["safety_queue"] if c["doc_id"] in docs)
    state["claims"] = [c for c in state["claims"] if c["doc_id"] not in docs]
    state["safety_queue"] = [c for c in state["safety_queue"] if c["doc_id"] not in docs]
    state["hypotheses"] = [h for h in state["hypotheses"] if h["id"] not in gone]
    for hid in gone:
        state["screens"].pop(hid, None)
    store.save(state)
    for d in (RUNTIME_DIR / "live", RUNTIME_DIR / "audio"):
        for p in d.glob("*") if d.exists() else []:
            p.unlink(missing_ok=True)
    return {"notes": sorted(n["doc_id"] for n in removed), "claims": n_claims, "safety": n_safety, "hypotheses": sorted(gone)}


def verify_github() -> dict[str, str]:
    """Read-only: compare each local script file with the same path on GitHub by sha256. Nothing fetched is written."""
    import httpx
    src = source_info()
    ref = src["ref"] or src["commit"]
    if not src["repo"] or not ref:
        raise SystemExit("GitHub 저장소를 알 수 없습니다 — COLLECT_GITHUB_REPO / COLLECT_GITHUB_REF 를 정한다.")
    out: dict[str, str] = {}
    with httpx.Client(timeout=10, verify=os.environ.get("SSL_CERT_FILE") or True, follow_redirects=True) as client:
        for p in sorted(SCRIPTS_DIR.glob("FS-*.json")):
            url = f"https://raw.githubusercontent.com/{src['repo']}/{ref}/{src['path']}/{p.name}"
            try:
                with client.stream("GET", url) as r:
                    if r.status_code == 404:
                        out[p.stem] = "missing"
                        continue
                    if r.status_code != 200:
                        out[p.stem] = "error"
                        continue
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > 64 * 1024:
                            break
                out[p.stem] = "same" if _sha(body) == _sha(p.read_bytes()) else "different"
            except httpx.HTTPError:
                out[p.stem] = "error"
    return out
