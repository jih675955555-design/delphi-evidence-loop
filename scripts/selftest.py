"""Offline self-test: fake model outputs, real sources (cached), real code paths. `uv run python scripts/selftest.py`
Checks: STT entrance (cache replay, consent, no duplicates, provenance hidden from the model, word boost off by default),
quote verification drops paraphrases, tally counts only verified claims, thresholds, gate order, action items,
TTS chunking and caching, the collection stage (listen, save gates, effect, undo), and finally the committed collection
bake replayed end to end with the real cache and no key (undo after 초기화 included), a model that stops answering
(deadline, watchdog, the next listen not refused), and .env as people edit it (a same-line comment is not
part of the value; a bad STT_BOOST is a reason on the page, never a 500)."""
import json, os, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loop import llm
llm.ENV_FILES = []                      # never read .env here: a cache miss must fail, not call the API
os.environ.pop("NVIDIA_API_KEY", None)
from loop import store, sense, screen, board, stt, tts, collect
store.STATE = store.DATA / "state.selftest.json"
store.FIELD_CHECKLIST = store.DATA / "field_checklist.selftest.json"
if store.STATE.exists(): store.STATE.unlink()

RULES = [("유방암", "유방암 환자", "REPURPOSING"), ("PCOS", "PCOS 여성", "OFF_LABEL_USE"),
         ("당뇨 전단계", "당뇨 전단계", "OFF_LABEL_DEMAND"), ("10세 미만", "소아 10세 미만", "OFF_LABEL_DEMAND"),
         ("젖산산증", "노인 65+ · 신기능 저하", "SAFETY_TOLERABILITY"), ("B12", "당뇨 전단계", "SAFETY_TOLERABILITY")]

def fake(purpose, *, system, user, schema_name, schema, **kw):
    try:
        data = json.loads(user)
    except ValueError:
        data = None   # board turns carry text, not JSON
    if purpose == "sense":
        claims = []
        for sent in re.split(r"(?<=[.다])\s+", data["text"]):
            for kw_, seg, sig in RULES:
                if kw_ in sent:
                    claims.append({"segment": seg, "signal_type": sig, "quote": sent.strip(),
                                   "is_adverse_event": kw_ in ("젖산산증", "B12"), "note_ko": "테스트"}); break
        claims.append({"segment": "PCOS 여성", "signal_type": "OFF_LABEL_USE", "quote": "이 문장은 원문에 없습니다 — 버려져야 한다",
                       "is_adverse_event": False, "note_ko": "paraphrase"})
        return {"claims": claims}
    if purpose == "hypothesis":
        return {"statement_ko": f"{data['segment']}에서 {data['signal_type']} 신호가 반복된다", "statement_en": "test",
                "label_status_guess": "DEVELOPMENT", "pubmed_query": "metformin[Title] AND breast cancer[Title/Abstract]",
                "ctgov_condition": "breast cancer"}
    if purpose.startswith("screen_"):
        recs = data["records"]; items = []
        for i, r in enumerate(recs[:3]):
            items.append({"source_id": r["id"], "stance": "NEUTRAL" if purpose == "screen_label" else ["SUPPORTS", "CONTRADICTS", "NEUTRAL"][i % 3],
                          "quote": r["text"][:90], "note_ko": "테스트"})
        items.append({"source_id": recs[0]["id"], "stance": "SUPPORTS", "quote": "not in the record at all xyz", "note_ko": "bad quote"})
        items.append({"source_id": "PMID:0", "stance": "SUPPORTS", "quote": recs[0]["text"][:40], "note_ko": "bad id"})
        return {"items": items}
    if purpose == "board_convene":
        return {"hypothesis_type": "REPURPOSING", "lead": "CMO", "speaking_order": ["CMO", "RA_HEAD", "PV_HEAD", "RND_HEAD", "CFO", "CCO", "CEO"], "opening_ko": "개회합니다. 상정된 가설은 하나입니다."}
    if purpose == "board_facilitate":
        return {"utterance_ko": "CMO와 RA 총괄께 묻습니다.", "speakers": ["CMO", "RA_HEAD"], "question_ko": "근거의 무게를 어떻게 보십니까?", "phase_decision": "MOVE_TO_FINAL"}
    if purpose.startswith("board_opening_") or purpose.startswith("board_discussion") or purpose.startswith("board_final_"):
        ev = json.loads(user.split("[가설 패키지]\n", 1)[1].split("\n[회의 기록]", 1)[0])["evidence"]
        cited = [ev[0]["source_id"], "PMID:0"] if ev else ["PMID:0"]
        who = purpose.split("_", 2)[2].upper()   # board_final_rnd_head → RND_HEAD
        stance = "OPPOSE" if who in ("CMO", "RND_HEAD") else ("SUPPORT" if who == "CCO" else "HOLD")
        if purpose.startswith("board_final_") and who == "CEO": stance = "OPPOSE"
        action = "프로모션 메시지를 준비하겠습니다." if who == "CCO" else ""
        return {"stance": stance, "confidence": 4, "stance_changed": False, "utterance_ko": "결론부터 말씀드립니다. 근거가 그렇습니다.",
                "cited": cited, "question_ko": "다음 면담에서 확인할 것" if who == "CMO" else "", "action_ko": action}
    if purpose == "board_close":
        return {"summary_ko": "요약입니다.", "evidence_summary_ko": "근거 요약입니다.", "rationale_ko": "권고 사유입니다.", "kill_criteria_ko": ["중단 기준"],
                "risks_ko": ["위험"], "follow_up_questions": [{"question_ko": "다음 면담에서 물을 것", "why_ko": "이유"}], "closing_ko": "폐회합니다."}
    raise AssertionError(purpose)

sense.call_structured = screen.call_structured = board.call_structured = fake
state, contract = store.load(), store.contract()
notes = json.loads(store.FIELD_NOTES.read_text())
st = sense.run(state, contract, notes)
print("sense:", st)
assert st["dropped"] == len(notes), "every note had one paraphrase that must be dropped"
assert st["adverse_events"] == 2 and len(state["safety_queue"]) == 2
rows = sense.tally(state); print("tally:", [(r["segment"], r["signal_type"], r["mentions"], r["hcps"]) for r in rows])
assert all(c["verified"] for c in state["claims"] if c["char_start"] is not None)
created = sense.draft_hypotheses(state, contract, 3, 3)
print("hypotheses:", [(h["id"], h["segment"], h["field"]) for h in created])
assert {h["segment"] for h in created} == {"유방암 환자", "PCOS 여성", "당뇨 전단계"}, "threshold 3/3 must promote exactly three groups"
hyp = created[0]["id"]
try: board.deliberate(state, hyp); raise AssertionError("board ran without review signature")
except SystemExit as e: print("gate ok:", e)
s = screen.run(state, hyp, contract)
print("screen numbers:", {k: s["numbers"][k] for k in ("pubmed_hits", "ctgov_total", "ctgov_recruiting", "faers_total")})
print("screen tally:", s["totals"], "dropped", len(s["dropped"]), "label", s["label_status"])
assert len(s["dropped"]) == 6 and all(it["verified"] for it in s["items"])
board.sign_review(state, hyp, "테스터")
m = board.deliberate(state, hyp); print("board route:", m["route"], "· turns", len(m["transcript"]), "· tally", m["tally"]["counts"], "· reco", m["recommendation"], "· blocked", len(m["blocked_actions"]))
assert len(m["transcript"]) == 1 + 7 + 1 + 2 + 7 + 1 and m["recommendation"] == "DROP"
assert all("PMID:0" not in tr.get("cited", []) for tr in m["transcript"]), "invalid citations must be dropped"
assert len(m["blocked_actions"]) >= 1, "commercial action on DEVELOPMENT hypothesis must be blocked"
acts = board.approve(state, hyp, "테스터"); print("actions:", [a["id"] for a in acts], "checklist:", store.FIELD_CHECKLIST.exists())
print("status:", store.hypothesis(state, hyp)["status"])

# ── STT entrance: audio → transcript → field note → sense (the model is faked, the file handling is real) ──
import io, tempfile, wave
tmp = Path(tempfile.mkdtemp())
stt.CACHE_DIR, stt.RUNS_LOG = tmp / "stt_cache", tmp / "stt_runs.jsonl"
wav = tmp / "interview.wav"
with wave.open(str(wav), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000 * 3)
SAID = "PCOS 환자에게 메트포르민을 써봤는데 배란이 돌아온 경우가 있었다. 허가 범위 밖이라 설명이 조심스럽다."
calls = []
def fake_recognize(pcm, rate, terms, cfg, **kw):
    calls.append((len(pcm), rate, terms)); return SAID, [{"word": "PCOS", "start_ms": 0, "end_ms": 400, "confidence": 0.9}]
real_recognize, stt._recognize = stt._recognize, fake_recognize
os.environ.pop("STT_BOOST", None)
assert stt.settings("parakeet")["boost"] == 0.0, "word boosting must be off by default (it invented segment names)"
rec = stt.transcribe(wav, contract)
assert rec["text"] == SAID and rec["duration_s"] == 3.0 and len(calls) == 1 and calls[0][1] == 16000
assert calls[0][2] == [] and rec["keyterms"] == [] and rec["boost"] == 0.0, "no vocabulary is boosted by default"
assert stt.transcribe(wav, contract) == rec and len(calls) == 1, "same audio must replay from cache"
os.environ["STT_BOOST"] = "2"   # opt-in still works, and is a different cache entry
boosted = stt.transcribe(wav, contract)
assert len(calls) == 2 and "메트포르민" in calls[1][2] and "PCOS 여성" in calls[1][2] and boosted["boost"] == 2.0
os.environ.pop("STT_BOOST")
notes_tmp = tmp / "field_notes.json"; notes_tmp.write_text(store.FIELD_NOTES.read_text())
try: stt.add_note(rec, hcp_ref="HCP-13", specialty="산부인과", date="2026-07-02", consent_by=" ", audio_name="a.wav", notes_path=notes_tmp); raise AssertionError("note without consent")
except SystemExit as e: print("consent gate ok:", e)
note = stt.add_note(rec, hcp_ref="HCP-13", specialty="산부인과", date="2026-07-02", consent_by="테스터", audio_name="a.wav", notes_path=notes_tmp)
assert note["doc_id"] == "FN-2026-0702-02", note["doc_id"]   # FN-2026-0702-01 already exists
try: stt.add_note(rec, hcp_ref="HCP-13", specialty="산부인과", date="2026-07-02", consent_by="테스터", audio_name="a.wav", notes_path=notes_tmp); raise AssertionError("same audio added twice")
except SystemExit as e: print("duplicate gate ok:", e)
all_notes = json.loads(notes_tmp.read_text())
assert all_notes[:-1] == notes and all_notes[-1] == note, "existing notes must be untouched"
assert notes_tmp.read_text().startswith(store.FIELD_NOTES.read_text().rstrip()[:-1].rstrip()), "hand formatting must be kept"
seen_users = []
def fake_sense(purpose, *, user, **kw):
    seen_users.append(user); return {"claims": [{"segment": "PCOS 여성", "signal_type": "OFF_LABEL_USE", "quote": SAID.split(". ")[0] + ".", "is_adverse_event": False, "note_ko": "테스트"}]}
sense.call_structured = fake_sense
st2 = sense.run(json.loads(json.dumps(store.EMPTY)), contract, [note])
assert st2 == {"docs": 1, "kept": 1, "dropped": 0, "adverse_events": 0}, st2
assert '"stt"' not in seen_users[0] and "audio_sha256" not in seen_users[0], "provenance must not reach the model"
assert stt.settings("whisper")["boost"] == 0 and stt.settings("parakeet")["language"] == "ko-KR"
pcm = (b"\x10\x00" * 16000 * 7 + b"\x00\x00" * 16000) * 16          # 128 s: 7 s sound, 1 s silence
segs = stt._segments(pcm, 16000)
assert b"".join(segs) == pcm and all(len(s) <= 60 * 32000 for s in segs), "offline segments must be lossless and ≤ 60 s"
assert abs(stt.cer("가나다라", "가나다라") - 0) < 1e-9 and abs(stt.cer("가 나, 다라.", "가나다마") - 0.25) < 1e-9, "CER ignores spaces and punctuation"
print("stt:", note["doc_id"], rec["duration_s"], "s ·", len(rec["keyterms"]), "keyterms (boost off) · sense on transcript", st2)
# a model that stops answering: every request carries a deadline, and a live listener is not re-streamed from 0 s
import grpc, time, types
import riva.client as riva_client
class Deadline(grpc.RpcError):
    def code(self): return grpc.StatusCode.DEADLINE_EXCEEDED
    def details(self): return "Deadline Exceeded"
class HungStub:   # takes the audio and never answers until its deadline — what NVCF did in 1 of 5 live runs
    calls = []
    def StreamingRecognize(self, requests, metadata=None, timeout=None):
        HungStub.calls.append(("stream", timeout))
        def responses():
            time.sleep(timeout); raise Deadline()
            yield
        return responses()
    def Recognize(self, request, metadata=None, timeout=None):
        HungStub.calls.append(("offline", timeout)); raise Deadline()
real_service, real_stall = stt._service, stt.STALL_S
stt._service = lambda cfg: (riva_client, types.SimpleNamespace(stub=HungStub(), auth=types.SimpleNamespace(get_auth_metadata=lambda: [])))
stt._recognize, stt.STALL_S = real_recognize, 0.3
t_hang, cached_before = time.time(), set(stt.CACHE_DIR.glob("*.json"))
for eng, pace_ in (("parakeet", 20), ("whisper", 0)):
    try: stt.transcribe(wav, contract, force=True, engine=eng, on_partial=lambda f, i: None, pace=pace_); raise AssertionError(f"{eng}: a stalled request returned")
    except stt.SttStalled as e: assert "멈췄습니다" in str(e) and "다시 누르면" in str(e), e
assert HungStub.calls == [("stream", 3.0 / 20 + 0.3), ("offline", 3.0 + 0.3)], HungStub.calls   # one try each, a finite deadline
assert time.time() - t_hang < 3, "a stall is not retried for a live listener"
assert set(stt.CACHE_DIR.glob("*.json")) == cached_before, "a stalled request is never cached"
stt._service, stt._recognize, stt.STALL_S = real_service, fake_recognize, real_stall
print("stt stall: deadline", HungStub.calls, "→ SttStalled, no retry while someone is listening")

# ── TTS: chunking, caching, truncation split (the network call is faked) ──
import hashlib, shutil, threading
tts.CACHE_DIR, tts.RUNS_LOG = tmp / "tts_cache", tmp / "tts_runs.jsonl"
scripts = collect.list_scripts(contract)
assert [x["script_id"] for x in scripts] == ["FS-01", "FS-02", "FS-03", "FS-04"] and not any("_error" in x for x in scripts), scripts
LONG = " ".join(["가나다라마바사아자"] * 40)   # 399 chars, no sentence end
for text in [x["text"] for x in scripts] + [LONG]:
    ch = tts.chunks(text)
    assert " ".join(ch) == " ".join(text.split()), "chunking must be lossless"
    assert all(len(c) <= tts.MAX_CHUNK_CHARS for c in ch), [len(c) for c in ch]
    if text is not LONG:
        assert all(c.endswith((".", "?", "!")) for c in ch), "script chunks end at sentence boundaries"
synth = []
def fake_chunk(text, cfg):
    if fake_chunk.truncate and len(text) > 60:
        raise RuntimeError("StatusCode.INVALID_ARGUMENT: Audio generation was truncated: output reached the maximum allowed length")
    synth.append(text); return b"" if fake_chunk.empty else b"\x01\x00" * (240 * len(text))   # 10 ms per character
fake_chunk.truncate = fake_chunk.empty = False
tts._synth_chunk = fake_chunk
t1 = tts.synthesize(scripts[0]["text"])
pieces = tts.chunks(scripts[0]["text"])
assert synth == pieces and t1["chunks"] == len(pieces), "one request per chunk, in order"
with wave.open(t1["path"]) as w:
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
expect = tts.LEAD_S + sum(0.01 * len(c) for c in pieces) + tts.GAP_S * (len(pieces) - 1) + tts.TAIL_S
assert abs(t1["duration_s"] - expect) < 0.011, (t1["duration_s"], expect)
assert tts.synthesize(scripts[0]["text"]) == t1 and len(synth) == len(pieces), "same text must replay from cache"
fake_chunk.empty = True
try: tts.synthesize("비어 있는 합성을 시험한다."); raise AssertionError("empty audio accepted")
except tts.TtsUnavailable as e: print("tts empty ok:", e)
assert not list(tts.CACHE_DIR.glob(f"{tts._cache_key(tts.settings(), '비어 있는 합성을 시험한다.')}*")), "empty audio must not be cached"
fake_chunk.empty, fake_chunk.truncate, synth[:] = False, True, []
t2 = tts.synthesize(scripts[1]["text"])
assert t2["chunks"] == 2 and len(synth) == 4 and all(len(x) <= 60 for x in synth), "a truncated chunk is split in half and retried"
fake_chunk.truncate = False
print("tts:", [len(c) for c in pieces], "chars/chunk ·", t1["duration_s"], "s · truncation split", [len(x) for x in synth])

# ── the scripts themselves (rules measured on the TTS → STT round trip) ──
orig_hcps, orig_dates = {n["hcp_ref"] for n in notes}, {n["date"] for n in notes}
orig_sents = {x.strip() for n in notes for x in re.split(r"(?<=[.다])\s+", n["text"]) if len(x.strip()) > 10}
for x in scripts:
    assert x["synthetic"] is True and not re.search(r"[A-Za-z]", x["text"]) and 150 <= len(x["text"]) <= 230, x["script_id"]
    assert x["hcp_ref"] not in orig_hcps and x["date"] not in orig_dates, x["script_id"]
    assert not any(o in x["text"] for o in orig_sents), f"{x['script_id']} reuses a sentence of an existing note"
assert len({x["date"] for x in scripts}) == len(scripts) and len({x["hcp_ref"] for x in scripts}) == len(scripts)

# ── collection: validation, replay listening, save gates, provenance hidden, effect, undo (models faked) ──
real_scripts_dir = collect.SCRIPTS_DIR
collect.SCRIPTS_DIR = tmp / "bad_scripts"; collect.SCRIPTS_DIR.mkdir()
(collect.SCRIPTS_DIR / "FS-90.json").write_text("{깨진 JSON")
(collect.SCRIPTS_DIR / "FS-91.json").write_text(json.dumps({**{k: v for k, v in scripts[0].items() if k in json.loads((real_scripts_dir / "FS-01.json").read_text())},
                                                           "script_id": "FS-91", "intent": {"segment": "없는 환자군", "signal_type": "DOSING", "why_ko": ""}}, ensure_ascii=False))
bad = collect.list_scripts(contract)
assert [b["script_id"] for b in bad] == ["FS-90", "FS-91"] and all("_error" in b for b in bad), bad
for sid in ("FS-90", "../state", "fs-01", "FS-99"):
    try: collect.get(sid); raise AssertionError(f"bad script id accepted: {sid}")
    except SystemExit: pass
collect.SCRIPTS_DIR = real_scripts_dir
collect.RUNTIME_DIR = tmp / "collect_runtime"
def fake_audio(script, data):   # a runtime MP3 + sidecar, as make_audio would leave them
    d = collect.RUNTIME_DIR / "audio"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{script['script_id']}.mp3").write_bytes(data)
    (d / f"{script['script_id']}.json").write_text(json.dumps({"script_id": script["script_id"], "text_sha256": script["text_sha256"], "audio_sha256": hashlib.sha256(data).hexdigest(),
        "duration_s": 3.0, "tts": {"model": tts.ENGINE["model"], "voice": tts.ENGINE["voice"], "function_id": tts.ENGINE["function_id"], "chunks": 1, "created_at": store.now()}}))
    return collect.audio(script)
fs1, fs2 = scripts[0], scripts[1]
a1 = fake_audio(fs1, b"ID3-fake-audio-FS-01")
assert a1["source"] == "runtime" and collect.saved_transcript(fs1) is None
HEARD = fs1["text"].replace("메트포르민", "매트포르밍")   # one mishearing, like the real round trip
words = [{"word": w, "start_ms": 100 * i, "end_ms": 100 * i + 80, "confidence": 0.9} for i, w in enumerate(HEARD.split())]
stt.CACHE_DIR.mkdir(parents=True, exist_ok=True)
fake_rec = {"engine": "parakeet", "model": stt.ENGINES["parakeet"]["model"], "language": "ko-KR", "audio_sha256": a1["audio_sha256"], "duration_s": 3.0,
            "keyterms": [], "boost": 0.0, "cache_key": collect.stt_key(a1), "created_at": store.now(), "text": HEARD, "words": words}
(stt.CACHE_DIR / f"{fake_rec['cache_key']}.json").write_text(json.dumps(fake_rec, ensure_ascii=False))
try: collect.listen("FS-01", mode="live", background=False); raise AssertionError("live listen without a key")
except collect.ListenRefused as e: assert e.code == "NO_KEY", e.code
seen_status, real_write = [], collect._write_live
collect._write_live = lambda sid, r: (seen_status.append((r["status"], len(" ".join(r["finals"]) + r["interim"]))), real_write(sid, r))
lv = collect.listen("FS-01", mode="replay", pace=0, background=False)
collect._write_live = real_write
assert seen_status[0][0] == "RUNNING" and seen_status[-1][0] == "DONE" and lv["status"] == "DONE", seen_status[:3]
grow = [n for st_, n in seen_status if st_ == "RUNNING"]
assert grow == sorted(grow) and grow[-1] > grow[1], "replay reveals the transcript word by word"
assert lv["text"] == HEARD and lv["cer"] == stt.cer(HEARD, fs1["text"]) and collect.live_read("FS-01")["rec"]["cache_key"] == fake_rec["cache_key"]
# a live listen whose stream stalls: the record says ERROR with the reason, nothing stays «듣는 중», the next listen starts
from loop import pages
real_transcript, real_can, real_watchdog = collect.transcript, collect.can_call_models, collect.WATCHDOG_S
release = threading.Event()
def stalling_transcript(script, *, mode="cached", **kw):
    if mode != "live": return real_transcript(script, mode=mode, **kw)
    kw["on_partial"](["요양병원에 계신"], "여든")
    if stalling_transcript.hang:   # a thread that never comes back, deadline or not — the watchdog's case
        release.wait(); return fake_rec
    raise stt.SttStalled("STT 응답이 멈췄습니다 — 시험. 다시 누르면 처음부터 듣습니다.")
collect.transcript, collect.can_call_models, stalling_transcript.hang = stalling_transcript, (lambda: True), False
lv_s = collect.listen("FS-01", mode="live", pace=0, background=False)
assert lv_s["status"] == "ERROR" and "멈췄습니다" in lv_s["error"] and collect.busy() is None, lv_s
stalling_transcript.hang, collect.WATCHDOG_S, stt.STALL_S = True, 0.2, 0.1
collect.listen("FS-01", mode="live", pace=50, background=True)
assert collect.busy() == "FS-01" and collect.live_read("FS-01")["status"] == "RUNNING"
try: collect.listen("FS-02", mode="replay"); raise AssertionError("a second listen started while one runs")
except collect.ListenRefused as e: assert e.code == "BUSY", e.code
time.sleep(3.0 / 50 + 0.1 + 0.2 + 0.2)   # past the watchdog time: the thread is still stuck, the record is not
stuck = collect.live_read("FS-01")
assert collect._THREADS["FS-01"].is_alive() and stuck["status"] == "ERROR" and "멈췄습니다" in stuck["error"] and collect.busy() is None, stuck
html = pages.collect_script_page(store.load(), contract, "FS-01")
assert "이전 듣기가 중단됐다 — STT 응답이 멈췄습니다" in html and 'data-listen="replay"' in html and "RUNNING=false" in html
again = collect.listen("FS-01", mode="replay", pace=0, background=False)
assert again["status"] == "DONE" and again["mode"] == "replay"
release.set(); collect._THREADS["FS-01"].join(5)
assert not collect._THREADS["FS-01"].is_alive() and collect.live_read("FS-01")["mode"] == "replay", "a late write from the stuck thread is dropped"
collect.transcript, collect.can_call_models, collect.WATCHDOG_S, stt.STALL_S = real_transcript, real_can, real_watchdog, real_stall
print("collect stall: deadline → ERROR · stuck thread → watchdog ERROR, BUSY released, page offers replay, late write dropped")
notes_c = tmp / "field_notes_collect.json"; shutil.copy(store.FIELD_NOTES, notes_c)
real_notes, store.FIELD_NOTES = store.FIELD_NOTES, notes_c
original_bytes = notes_c.read_bytes()
try: collect.save(fs1, "  ", lv["rec"], listen_mode="replay"); raise AssertionError("saved without consent")
except SystemExit as e: print("collect consent gate ok:", e)
try: collect.save(fs1, "테스터", {**lv["rec"], "audio_sha256": "0" * 64}, listen_mode="replay"); raise AssertionError("saved a transcript of other audio")
except SystemExit as e: print("collect audio gate ok:", e)
cn = collect.save(fs1, "테스터", lv["rec"], listen_mode="replay")
assert cn["doc_id"] == "FN-2026-0921-01" and cn["synthetic"] is True and cn["hcp_ref"] == fs1["hcp_ref"] and cn["text"] == HEARD
t_ = cn["stt"]
assert (t_["source"], t_["script_id"], t_["engine"], t_["tts"]["model"], t_["consent_by"]) == ("script", "FS-01", "parakeet", "resembleai/chatterbox-multilingual-tts", "테스터")
assert t_["audio_sha256"] == hashlib.sha256((collect.RUNTIME_DIR / "audio" / "FS-01.mp3").read_bytes()).hexdigest() and t_["cer"] == round(stt.cer(HEARD, fs1["text"]), 4)
assert list(cn) == ["doc_id", "hcp_ref", "specialty", "date", "synthetic", "text", "stt"], "top-level key order unchanged"
assert notes_c.read_text().startswith(original_bytes.decode().rstrip()[:-1].rstrip()) and json.loads(notes_c.read_text())[:-1] == notes
try: collect.save(fs1, "테스터", lv["rec"], listen_mode="replay"); raise AssertionError("same script saved twice")
except SystemExit as e: assert "이미 수집한 대본입니다 — FN-2026-0921-01" in str(e), e
a2 = fake_audio(fs2, b"ID3-fake-audio-FS-01")            # another script id, the same recording
try: collect.save(fs2, "테스터", {**lv["rec"], "audio_sha256": a2["audio_sha256"]}, listen_mode="replay"); raise AssertionError("same audio saved twice")
except SystemExit as e: assert "이미 전사해 넣은 음성입니다" in str(e), e
seen_users.clear(); sense.call_structured = fake_sense
col_state = json.loads(json.dumps(store.EMPTY))
sense.run(col_state, contract, [cn])
assert all(x not in seen_users[0] for x in ('"stt"', "intent", "script_id", "audio_sha256", "테스터")), "provenance and intent never reach the model"
# effect on a hand-made state: two old claims + three from the collected note → the group crosses 3/2 → 5/3
mk = lambda cid, doc, hcp, seg="노인 65+ · 신기능 저하", sig="DOSING", ok=True: {"id": cid, "doc_id": doc, "hcp_ref": hcp, "segment": seg, "signal_type": sig, "verified": ok, "quote": "q"}
eff_state = {"claims": [mk("C1", "FN-A", "HCP-01"), mk("C2", "FN-A", "HCP-01"), mk("C3", "FN-B", "HCP-09"),
                        mk("C4", cn["doc_id"], "HCP-13"), mk("C5", cn["doc_id"], "HCP-13"), mk("C6", cn["doc_id"], "HCP-13", ok=False)],
             "safety_queue": [mk("C7", cn["doc_id"], "HCP-13", sig="SAFETY_TOLERABILITY")],
             "hypotheses": [{"id": "HYP-001", "segment": "노인 65+ · 신기능 저하", "signal_type": "DOSING", "field": {"claim_ids": ["C1", "C2", "C3", "C4", "C5"]}, "status": "DRAFT"}]}
eff = collect.effect(eff_state, contract, json.loads(notes_c.read_text()))
r0 = eff["rows"][0]
assert len(eff["rows"]) == 1 and r0["before"] == [3, 2] and r0["after"] == [5, 3] and r0["passed_after"] and not r0["passed_before"], eff
assert eff["new_hypotheses"] == ["HYP-001"] and r0["new_hypothesis"] and eff["safety_added"] == 1 and eff["dropped"] == 1 and eff["pending_docs"] == []
# undo: only what collection added; refuses once a person signed something derived from it
store.STATE = tmp / "state_collect.json"
signed = {"claims": eff_state["claims"], "safety_queue": eff_state["safety_queue"], "screens": {}, "reviews": {"HYP-001": {"by": "x"}}, "board": {}, "actions": [],
          "hypotheses": [{**eff_state["hypotheses"][0], "status": "REVIEWED"}]}
store.save(signed)
try: collect.uncollect(); raise AssertionError("undo deleted a signed hypothesis")
except SystemExit as e: print("undo refusal ok:", e)
assert json.loads(notes_c.read_text())[-1]["doc_id"] == cn["doc_id"], "a refused undo changes nothing"
orig_hyp = {"id": "HYP-002", "segment": "PCOS 여성", "signal_type": "OFF_LABEL_USE", "field": {"claim_ids": ["C1"]}, "status": "SCREENED"}
store.save({**signed, "reviews": {}, "hypotheses": [eff_state["hypotheses"][0], orig_hyp], "screens": {"HYP-002": {}}})
try: collect.uncollect(); raise AssertionError("undo renumbered a screened hypothesis")
except SystemExit as e:
    assert "HYP-002 은(는) 원래 면담에서 나온 가설" in str(e) and "«초기화»를 누른 뒤" in str(e) and "«수집 되돌리기»를 한 번 더" in str(e), e
assert notes_c.read_bytes() != original_bytes and len(store.load()["hypotheses"]) == 2, "a refused undo changes nothing"
store.save({**signed, "reviews": {}, "hypotheses": [eff_state["hypotheses"][0]], "screens": {"HYP-001": {}}})
u = collect.uncollect()
assert u == {"notes": [cn["doc_id"]], "claims": 3, "safety": 1, "hypotheses": ["HYP-001"], "renumbered": {}}, u
assert notes_c.read_bytes() == original_bytes, "undo must leave the notes file byte-identical"
left = store.load()
assert [c["id"] for c in left["claims"]] == ["C1", "C2", "C3"] and not left["safety_queue"] and not left["hypotheses"] and not left["screens"]
assert not list((collect.RUNTIME_DIR / "live").glob("*.json")), "undo clears the live listening records"
try: collect.uncollect(); raise AssertionError("undo with nothing collected")
except SystemExit: pass
store.FIELD_NOTES = real_notes
print("collect:", cn["doc_id"], "· replay listen", len(seen_status), "writes · effect", r0["before"], "→", r0["after"], "· undo", u)

# ── 대본 수집 재생 — 커밋된 음성·전사·모델 응답, 키 없음 (real llm_cache, no fakes, no network) ──
sense.call_structured = llm.call_structured
assert not collect.can_call_models()
collect.RUNTIME_DIR, stt.CACHE_DIR = tmp / "rt_real", tmp / "stt_empty"   # only the committed bake may answer
bake = json.loads((collect.SCRIPTS_DIR / "bake.json").read_text())
assert bake["boost"] == 0 and set(bake["scripts"]) == {x["script_id"] for x in scripts}
assert all((llm.CACHE_DIR / f"{k}.json").exists() for k in bake["llm_keys"]), "every model output the bake used is committed"
cache_before = set(os.listdir(llm.CACHE_DIR))
assert sum(p.stat().st_size for p in collect.SCRIPTS_DIR.rglob("*") if p.is_file()) <= 1.5 * 1024 * 1024
store.FIELD_NOTES = tmp / "field_notes_real.json"; shutil.copy(real_notes, store.FIELD_NOTES)
store.STATE = tmp / "state_real.json"; shutil.copy(store.DATA / "state.json", store.STATE)
for x in scripts:
    a = collect.audio(x); b = bake["scripts"][x["script_id"]]
    assert a and a["source"] == "baked" and a["audio_sha256"] == b["audio_sha256"] and a["duration_s"] <= 40
    assert (collect.SCRIPTS_DIR / "audio" / f"{x['script_id']}.mp3").stat().st_size <= 400 * 1024
    r = collect.transcript(x, mode="replay")
    assert r["cache_key"] == b["stt_cache_key"] and r["boost"] == 0 and round(stt.cer(r["text"], x["text"]), 4) == b["cer"] <= 0.15
    collect.save(x, "셀프테스트", r, listen_mode="replay")
lv = collect.listen("FS-01", mode="replay", pace=0, background=False)
assert lv["status"] == "DONE" and lv["text"] == collect.transcript(scripts[0], mode="replay")["text"]
contract = store.contract()
for label in ("committed_state", "fresh_state"):
    if label == "fresh_state":
        store.STATE.unlink()
    st_r = store.load(); notes_r = json.loads(store.FIELD_NOTES.read_text())
    got = sense.run(st_r, contract, notes_r)
    made = sense.draft_hypotheses(st_r, contract, contract["threshold"]["min_mentions"], contract["threshold"]["min_hcps"])
    eff_r = collect.effect(st_r, contract, notes_r)
    want = bake["effect"][label]
    assert got == want["sense"], (label, got, want["sense"])
    assert [{"id": h["id"], "segment": h["segment"], "signal_type": h["signal_type"], "mentions": h["field"]["mentions"], "hcps": h["field"]["hcps"]} for h in made] == want["created"], label
    assert eff_r["rows"] == want["rows"] and eff_r["safety_added"] == want["safety_added"], label
    old = [h for h in made if (h["segment"], h["signal_type"]) == ("노인 65+ · 신기능 저하", "DOSING")]
    assert len(old) == 1 and old[0]["status"] == "DRAFT" and old[0]["field"]["mentions"] >= 5 and old[0]["field"]["hcps"] >= 3, label
    if label == "fresh_state":
        assert ("PCOS 여성", "OFF_LABEL_USE") in {(h["segment"], h["signal_type"]) for h in made}
    print(f"collect replay ({label}):", got, "→", [(h["id"], h["segment"], h["signal_type"], h["field"]["mentions"], h["field"]["hcps"]) for h in made])
# undo after 초기화: one extraction drafted the collected group first (HYP-001) and PCOS from the original notes
# after it (HYP-002). Undo removes HYP-001 and moves PCOS up — the same hypothesis a clean extraction gives.
st_u = store.load()
assert [h["id"] for h in st_u["hypotheses"]] == ["HYP-001", "HYP-002"] and st_u["hypotheses"][1]["segment"] == "PCOS 여성", st_u["hypotheses"]
pcos_before = st_u["hypotheses"][1]
u_all = collect.uncollect()
assert u_all["hypotheses"] == ["HYP-001"] and u_all["renumbered"] == {"HYP-002": "HYP-001"} and len(u_all["notes"]) == 4, u_all
assert store.FIELD_NOTES.read_bytes() == real_notes.read_bytes(), "undo leaves the notes file byte-identical"
clean_state = json.loads(json.dumps(store.EMPTY)); real_state_path = store.STATE
store.STATE = tmp / "state_clean.json"
sense.run(clean_state, contract, json.loads(real_notes.read_text()))
clean_made = sense.draft_hypotheses(clean_state, contract, contract["threshold"]["min_mentions"], contract["threshold"]["min_hcps"])
store.STATE = real_state_path
st_u = store.load()
strip_t = lambda h: {k: v for k, v in h.items() if k != "created_at"}   # noqa: E731
assert [strip_t(h) for h in st_u["hypotheses"]] == [strip_t(h) for h in clean_made] == [{**strip_t(pcos_before), "id": "HYP-001"}], "undo = extraction without the collection"
strip_c = lambda cs: [{k: v for k, v in c.items() if k != "extracted_at"} for c in cs]   # noqa: E731
assert strip_c(st_u["claims"]) == strip_c(clean_state["claims"]) and strip_c(st_u["safety_queue"]) == strip_c(clean_state["safety_queue"])
# the evaluator's path: 초기화 → FS-01 alone → ① 추출 → «수집 되돌리기» → ① 추출 again finds nothing new
store.STATE.unlink()
lv1 = collect.listen("FS-01", mode="replay", pace=0, background=False)
collect.save(scripts[0], "셀프테스트", lv1["rec"], listen_mode="replay")
st_1 = store.load(); sense.run(st_1, contract, json.loads(store.FIELD_NOTES.read_text()))
made_1 = sense.draft_hypotheses(st_1, contract, contract["threshold"]["min_mentions"], contract["threshold"]["min_hcps"])
assert [(h["id"], h["segment"]) for h in made_1] == [("HYP-001", "노인 65+ · 신기능 저하"), ("HYP-002", "PCOS 여성")], made_1
u_1 = collect.uncollect()
assert u_1["renumbered"] == {"HYP-002": "HYP-001"} and store.FIELD_NOTES.read_bytes() == real_notes.read_bytes(), u_1
st_1 = store.load(); sense.run(st_1, contract, json.loads(store.FIELD_NOTES.read_text()))
assert sense.draft_hypotheses(st_1, contract, contract["threshold"]["min_mentions"], contract["threshold"]["min_hcps"]) == []
assert [strip_t(h) for h in st_1["hypotheses"]] == [strip_t(h) for h in clean_made]
print("collect undo after 초기화:", u_all, "· FS-01 alone:", u_1["hypotheses"], u_1["renumbered"], "→ same as a clean extraction")
assert set(os.listdir(llm.CACHE_DIR)) == cache_before, "replay must not create model outputs"

# ── web: the audio file, the refusals, and the console API seeing the new hypothesis ──
from fastapi.testclient import TestClient
from loop import web
store.STATE = tmp / "state_web.json"; shutil.copy(store.DATA / "state.json", store.STATE)
store.FIELD_NOTES = tmp / "field_notes_web.json"; shutil.copy(real_notes, store.FIELD_NOTES)
collect.RUNTIME_DIR = tmp / "rt_web"
client = TestClient(web.app)
r = client.get("/collect/FS-01/audio.mp3", headers={"Range": "bytes=0-99"})
assert r.status_code == 206 and r.headers["content-type"] == "audio/mpeg" and len(r.content) == 100, (r.status_code, r.headers)
for bad_path in ("/collect/..%2Fstate/audio.mp3", "/collect/FS-99/audio.mp3", "/collect/fs-01/audio.mp3"):
    assert client.get(bad_path).status_code == 404, bad_path
assert client.get("/collect/FS-99", follow_redirects=False).status_code == 303
assert client.post("/collect/FS-01/listen?mode=live").json()["error"]["code"] == "NO_KEY"
hold = threading.Event(); fake_thread = threading.Thread(target=hold.wait); fake_thread.start(); collect._THREADS["FS-02"] = fake_thread
rb = client.post("/collect/FS-01/listen?mode=replay")
assert rb.status_code == 409 and rb.json()["error"]["code"] == "BUSY"
hold.set(); fake_thread.join(); collect._THREADS.clear()
assert client.get("/collect").status_code == 200 and "FS-04" in client.get("/collect").text
rows_j = client.get("/collect/scripts.json").json()
assert [x["script_id"] for x in rows_j] == ["FS-01", "FS-02", "FS-03", "FS-04"] and rows_j[0]["current"] == {"mentions": 3, "hcps": 2, "passed": False}
collect.listen("FS-01", mode="replay", pace=0, background=False)
assert client.post("/run/collect/save", data={"script": "FS-01", "consent_by": ""}, follow_redirects=False).status_code == 303
assert "거부" in web.LAST["msg"] and len(json.loads(store.FIELD_NOTES.read_text())) == len(notes), web.LAST["msg"]
client.post("/run/collect/save", data={"script": "FS-01", "consent_by": "평가자"}, follow_redirects=False)
assert "FN-2026-0921-01" in web.LAST["msg"] and "동의 확인 평가자" in web.LAST["msg"], web.LAST["msg"]
client.post("/run/collect/sense", follow_redirects=False)
assert "새 가설 1개 (HYP-006)" in web.LAST["msg"], web.LAST["msg"]
page = client.get("/collect").text
assert "3회/2인" in page and "/hypotheses/HYP-006" in page and "대본 수집 · FS-01" in client.get("/notes").text
hyps_api = client.get("/api/hypotheses").json()
assert "HYP-006" in json.dumps(hyps_api), "the console API sees the collected hypothesis"
client.post("/run/collect/reset", follow_redirects=False)
assert store.FIELD_NOTES.read_bytes() == real_notes.read_bytes() and store.STATE.read_bytes() == (store.DATA / "state.json").read_bytes(), web.LAST["msg"]
print("web: audio 206 · traversal 404 · NO_KEY/BUSY 409 · save → sense → HYP-006 → /api/hypotheses · undo restores both files byte for byte")

# ── .env as the template is used: a line switched on with its same-line comment left in place, and a bad value ──
from loop import cli
env_tmp, ENV_KEYS = tmp / "dotenv", ("STT_ENGINE", "STT_LANGUAGE", "STT_BOOST", "TTS_VOICE", "COLLECT_GITHUB_REF")
def use_env(text):
    for k in ENV_KEYS: os.environ.pop(k, None)
    env_tmp.write_text(text); llm.ENV_FILES = [env_tmp]   # no NVIDIA_API_KEY in it: still offline
use_env("STT_ENGINE=parakeet        # parakeet (기본) | whisper\nSTT_LANGUAGE=ko-KR         # parakeet 기본 ko-KR\n"
        "STT_BOOST=2                # 기본 0 = 단어 가중 없음. parakeet 만\n"
        'export TTS_VOICE="Chatterbox-Multilingual.ko-KR.Male # 따옴표 안은 값"\nCOLLECT_GITHUB_REF=a#b\n')
cfg = stt.settings()
assert (cfg["engine"], cfg["language"], cfg["boost"]) == ("parakeet", "ko-KR", 2.0), cfg
assert tts.settings()["voice"] == "Chatterbox-Multilingual.ko-KR.Male # 따옴표 안은 값" and os.environ["COLLECT_GITHUB_REF"] == "a#b"
for path in ("/collect", "/collect/FS-01", "/collect/scripts.json"):
    assert client.get(path).status_code == 200, path
use_env("STT_BOOST=\n")
assert stt.settings("parakeet")["boost"] == 0.0, "an empty value is the default"
for bad in ("abc", "-1", "nan"):
    use_env(f"STT_BOOST={bad}\n")
    try: stt.settings("parakeet"); raise AssertionError(f"STT_BOOST={bad} accepted")
    except stt.SttUnavailable as e: assert "STT_BOOST" in str(e)
    assert stt.settings("whisper")["boost"] == 0.0, "whisper has no boosting to misconfigure"
    for path in ("/collect", "/collect/FS-01"):
        r = client.get(path)
        assert r.status_code == 200 and "STT 설정을 쓸 수 없다" in r.text and 'data-listen="' not in r.text, (bad, path, r.status_code)
    rl = client.post("/collect/FS-01/listen?mode=replay")
    assert rl.status_code == 409 and rl.json()["error"]["code"] == "STT_CONFIG", rl.text
argv = sys.argv; sys.argv = ["loop", "scripts"]
try: cli.main(); raise AssertionError("CLI ran with a bad STT_BOOST")
except SystemExit as e: assert str(e).startswith("[사실] STT_BOOST"), e
finally: sys.argv = argv
use_env(""); llm.ENV_FILES = []
print("dotenv: same-line comments dropped, quotes kept · bad STT_BOOST → reason on /collect (200), 409 STT_CONFIG, CLI message")
print("SELFTEST OK")
