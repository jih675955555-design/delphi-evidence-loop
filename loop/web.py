"""Web console — the pages with the loop's controls. One process, one writer.

Every button runs the same code the CLI runs. The two human gates take a name; the name is what the
record keeps. Model steps replay from cache when the input is unchanged, so a demo click is instant
unless it is genuinely new work. The board runs in a background thread and streams its turns to the
meeting page; while it runs, other mutating actions are refused. Listening on the collection page also runs in
a background thread, one script at a time, and writes only its live file (data/collect_runtime/live/).
"""
from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import board, collect, compat, intro, live, pages, runner, screen, sense, store, stt

app = FastAPI(title="DELPHi — Evidence Loop")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
app.include_router(compat.router)   # the original console's API, same paths and shapes
LAST = runner.LAST


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    """JSON errors for the console (a bare 500 carries no CORS headers and the client only sees 'Failed to fetch')."""
    traceback.print_exc()
    return JSONResponse({"error": {"code": type(exc).__name__, "message_ko": f"서버 오류 — {str(exc)[:200]}"}}, status_code=500)


def _take_banner() -> str:
    msg, LAST["msg"] = LAST["msg"], ""
    return msg


def _do(label: str, fn, back: str = "/console"):
    busy = live.running()
    if busy:
        LAST["msg"] = f"{label} 보류 — {', '.join(busy)} 심의가 진행 중입니다. 끝날 때까지 다른 실행은 잠깁니다."
        return RedirectResponse(f"/hypotheses/{busy[0]}/board", status_code=303)
    try:
        LAST["msg"] = f"{label}: {fn()}"
    except SystemExit as e:        # the gates refuse with SystemExit — show the reason, don't crash
        LAST["msg"] = f"{label} 거부 — {e}"
    except Exception as e:  # noqa: BLE001
        LAST["msg"] = f"{label} 실패 — {type(e).__name__}: {str(e)[:300]}"
        traceback.print_exc()
    return RedirectResponse(back, status_code=303)


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(intro.render(store.load(), store.contract()))


@app.get("/console", response_class=HTMLResponse)
def console():
    return HTMLResponse(pages.overview(store.load(), store.contract(), _take_banner()))


@app.get("/notes", response_class=HTMLResponse)
def notes():
    return HTMLResponse(pages.notes_page(store.load(), store.contract(), _take_banner()))


@app.get("/claims", response_class=HTMLResponse)
def claims():
    return HTMLResponse(pages.claims_page(store.load(), store.contract(), _take_banner()))


@app.get("/hypotheses", response_class=HTMLResponse)
def hypotheses():
    return HTMLResponse(pages.hypotheses_page(store.load(), store.contract(), _take_banner()))


@app.get("/hypotheses/{hid}", response_class=HTMLResponse)
def hypothesis(hid: str):
    try:
        return HTMLResponse(pages.hypothesis_page(store.load(), store.contract(), hid, _take_banner()))
    except SystemExit:
        return RedirectResponse("/hypotheses", status_code=303)


@app.get("/hypotheses/{hid}/board", response_class=HTMLResponse)
def hypothesis_board(hid: str):
    try:
        return HTMLResponse(pages.board_page(store.load(), store.contract(), hid, _take_banner(), live_rec=live.read(hid)))
    except SystemExit:
        return RedirectResponse("/hypotheses", status_code=303)


@app.get("/hypotheses/{hid}/board.json")
def hypothesis_board_json(hid: str, after: int = 0):
    """Snapshot for the streaming meeting page: turns after `after`, and the status."""
    rec = live.read(hid)
    if rec is None:
        memo = store.load()["board"].get(hid)
        if memo and "transcript" in memo:
            return JSONResponse({"status": "DONE", "turns": [t for t in memo["transcript"] if t["no"] > after], "total": len(memo["transcript"])})
        return JSONResponse({"status": "NONE", "turns": [], "total": 0})
    return JSONResponse({"status": rec["status"], "turns": [t for t in rec["turns"] if t["no"] > after],
                         "total": len(rec["turns"]), "error": rec.get("error"), "updated_at": rec["updated_at"]})


@app.get("/checklist", response_class=HTMLResponse)
def checklist():
    return HTMLResponse(pages.checklist_page(store.load(), store.contract(), _take_banner()))


@app.get("/health")
def health():
    st = store.load()
    return JSONResponse({"ok": True, "claims": len(st["claims"]), "hypotheses": len(st["hypotheses"]),
                         "screened": len(st["screens"]), "actions": len(st["actions"]), "board_running": live.running()})


@app.get("/state.json")
def state_json():
    return JSONResponse(store.load())


def _sense_and_draft() -> str:
    """① 추출 — the same step whether it is started from the overview or from the collection page."""
    state, contract = store.load(), store.contract()
    notes = json.loads(store.FIELD_NOTES.read_text())
    st = sense.run(state, contract, notes)
    created = sense.draft_hypotheses(state, contract, contract["threshold"]["min_mentions"], contract["threshold"]["min_hcps"])
    return (f"면담 {st['docs']}건 · 인용 검증 통과 {st['kept']} · 버림 {st['dropped']} · 유해사례 후보 {st['adverse_events']}"
            f" → 새 가설 {len(created)}개" + (f" ({', '.join(h['id'] for h in created)})" if created else ""))


@app.post("/run/sense")
def run_sense():
    return _do("① 추출", _sense_and_draft)


@app.post("/run/transcribe")
def run_transcribe(audio: UploadFile = File(...), hcp: str = Form(...), specialty: str = Form(...), date: str = Form(...),
                   consent_by: str = Form(...), engine: str = Form("parakeet")):
    """Interview audio → transcript → field note. The recording itself is not kept — only its hash."""
    import tempfile

    def go():
        suffix = Path(audio.filename or "audio.wav").suffix or ".wav"
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"upload{suffix}"
            src.write_bytes(audio.file.read())
            rec = stt.transcribe(src, store.contract(), engine=engine)
        note = stt.add_note(rec, hcp_ref=hcp, specialty=specialty, date=date, consent_by=consent_by,
                            audio_name=audio.filename or src.name)
        return (f"{note['doc_id']} — {rec['duration_s']}초 → {len(rec['text'])}자 ({rec['model']}) · 동의 확인 {note['stt']['consent_by']}"
                " → 개요에서 추출을 실행하면 이 면담도 집계된다")
    return _do("⓪ 음성 전사", go, "/notes")


# ── ⓪ 현장 수집 — script (GitHub) → Chatterbox TTS → listen while Parakeet listens → field note ──

def _script_or_none(sid: str) -> dict | None:
    try:
        return collect.get(sid)
    except SystemExit:
        return None


@app.get("/collect", response_class=HTMLResponse)
def collect_list():
    return HTMLResponse(pages.collect_page(store.load(), store.contract(), _take_banner()))


@app.get("/collect/scripts.json")
def collect_scripts_json():
    """The script list as data — declared before /collect/{sid} so the path is not read as an id."""
    state, contract = store.load(), store.contract()
    return JSONResponse(pages.collect_rows(state, contract))


@app.get("/collect/{sid}", response_class=HTMLResponse)
def collect_script(sid: str):
    if not _script_or_none(sid):
        return RedirectResponse("/collect", status_code=303)
    return HTMLResponse(pages.collect_script_page(store.load(), store.contract(), sid, _take_banner()))


@app.get("/collect/{sid}/audio.mp3")
def collect_audio(sid: str):
    """The one file the listener hears and STT hears. Only a valid script id, only the audio dirs."""
    script = _script_or_none(sid)
    a = collect.audio(script) if script else None
    if not a:
        return JSONResponse({"error": {"code": "NOT_FOUND", "message_ko": "음성이 없습니다."}}, status_code=404)
    return FileResponse(a["path"], media_type="audio/mpeg", headers={"Cache-Control": "no-cache"})


@app.post("/collect/{sid}/listen")
def collect_listen(sid: str, mode: str = "live"):
    if not _script_or_none(sid):
        return JSONResponse({"error": {"code": "NOT_FOUND", "message_ko": f"대본이 없습니다: {sid[:40]}"}}, status_code=404)
    try:
        rec = collect.listen(sid, mode=mode)
    except collect.ListenRefused as e:
        return JSONResponse({"error": {"code": e.code, "message_ko": e.message_ko}}, status_code=409)
    return JSONResponse({"ok": True, "mode": rec["mode"], "engine": rec["engine"]})


@app.get("/collect/{sid}/listen.json")
def collect_listen_json(sid: str):
    rec = collect.live_read(sid) if _script_or_none(sid) else None
    if rec is None:
        return JSONResponse({"status": "NONE"})
    return JSONResponse({k: rec.get(k) for k in ("status", "mode", "engine", "model", "finals", "interim", "elapsed_s",
                                                  "duration_s", "text", "cer", "error", "updated_at")})


@app.post("/run/collect/tts")
def run_collect_tts(script: str = Form(...)):
    def go():
        s = collect.get(script)
        if not collect.can_call_models():
            raise SystemExit("음성을 만들려면 NVIDIA_API_KEY 가 필요합니다.")
        a = collect.make_audio(s)
        return f"{s['script_id']} 음성 {a['duration_s']}초 · {a['tts']['chunks']}조각 · {a['bytes'] // 1024} KB (Chatterbox Multilingual · ko-KR 남성)"
    return _do("② 음성 만들기", go, f"/collect/{script}" if _script_or_none(script) else "/collect")


@app.post("/run/collect/save")
def run_collect_save(script: str = Form(...), consent_by: str = Form("")):
    def go():
        s = collect.get(script)
        rec = collect.live_read(s["script_id"])
        if not rec or rec["status"] != "DONE" or not rec.get("rec"):
            raise SystemExit("STT가 받아 적은 글이 아직 없습니다 — 먼저 «재생하며 받아 적기»를 누르세요.")
        note = collect.save(s, consent_by, rec["rec"], listen_mode=rec["mode"])
        t = note["stt"]
        return (f"{note['doc_id']} — {t['duration_s']}초 → {len(note['text'])}자 ({t['engine'].capitalize()}) · "
                f"동의 확인 {t['consent_by']} → 다음: 추출")
    return _do("⓪ 수집", go, f"/collect/{script}" if _script_or_none(script) else "/collect")


@app.post("/run/collect/sense")
def run_collect_sense():
    return _do("① 추출", _sense_and_draft, "/collect#effect")


@app.post("/run/collect/reset")
def run_collect_reset():
    def go():
        r = collect.uncollect()
        moved = " · ".join(f"{a} → {b}" for a, b in r["renumbered"].items())
        return (f"면담 기록 {len(r['notes'])}건 · 발언 카드 {r['claims']} · 유해사례 후보 {r['safety']} · "
                f"가설 {len(r['hypotheses'])}개{' (' + ', '.join(r['hypotheses']) + ')' if r['hypotheses'] else ''}를 지웠다 — 원래 면담 기록은 그대로"
                + (f" · 원래 면담에서 나온 가설은 번호만 당겼다 ({moved})" if moved else ""))
    return _do("수집 되돌리기", go, "/collect")


@app.post("/run/collect/verify")
def run_collect_verify():
    ko = {"same": "같음", "different": "다름", "missing": "GitHub에 아직 없음", "error": "확인 실패"}

    def go():
        r = collect.verify_github()
        src = collect.source_info()
        return f"{src['repo']} @ {src['ref'] or src['commit'][:7]} — " + " · ".join(f"{k} {ko[v]}" for k, v in r.items())
    return _do("GitHub 원본과 대조", go, "/collect")


@app.post("/run/screen")
def run_screen(hyp: str = Form(...)):
    def go():
        s = screen.run(store.load(), hyp, store.contract())
        t = s["totals"]
        return f"{hyp} 지지 {t['SUPPORTS']} · 반대 {t['CONTRADICTS']} · 중립 {t['NEUTRAL']} · 버림 {len(s['dropped'])} · 라벨 {s['label_status']}"
    return _do("② 근거 교차검증", go, f"/hypotheses/{hyp}")


@app.post("/run/review")
def run_review(hyp: str = Form(...), by: str = Form(...), note: str = Form("")):
    def go():
        r = board.sign_review(store.load(), hyp, by.strip(), note.strip())
        return f"{r['by']} 이(가) {r['at']} 에 근거 {r['items_read']}건을 검토했다고 서명 — 심의 상정 가능"
    return _do("③ 서명", go, f"/hypotheses/{hyp}")


_start_board = runner.start_board


@app.post("/run/board")
def run_board(hyp: str = Form(...)):
    busy = live.running()
    if busy:
        return RedirectResponse(f"/hypotheses/{busy[0]}/board", status_code=303)
    state = store.load()
    if hyp not in state["reviews"]:
        LAST["msg"] = f"④ 심의 거부 — {hyp}: 사람의 근거 검토 서명이 없습니다."
        return RedirectResponse(f"/hypotheses/{hyp}", status_code=303)
    live.clear(hyp)
    _start_board(hyp)
    return RedirectResponse(f"/hypotheses/{hyp}/board", status_code=303)


@app.post("/run/approve")
def run_approve(hyp: str = Form(...), by: str = Form(...)):
    def go():
        acts = board.approve(store.load(), hyp, by.strip())
        return f"{by.strip()} 결정 — {len(acts)}개 질문이 다음 면담 체크리스트에 들어갔다."
    return _do("⑤ 결정", go, f"/hypotheses/{hyp}")


@app.post("/run/reset")
def run_reset():
    def go():
        for p in (store.STATE, store.FIELD_CHECKLIST):
            p.unlink(missing_ok=True)
        for p in live.LIVE_DIR.glob("*.json") if live.LIVE_DIR.exists() else []:
            p.unlink(missing_ok=True)
        return "결과를 지웠다 (캐시는 유지 · 수집한 면담 기록은 현장 수집의 «수집 되돌리기»로 지운다)"
    return _do("초기화", go)
