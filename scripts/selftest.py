"""Offline self-test: fake model outputs, real sources (cached), real code paths. `uv run python scripts/selftest.py`
Checks: STT entrance (cache replay, consent, no duplicates, provenance hidden from the model), quote verification drops paraphrases, tally counts only verified claims, thresholds, gate order, action items."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loop import store, sense, screen, board, stt
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
def fake_recognize(pcm, rate, terms, cfg):
    calls.append((len(pcm), rate, terms)); return SAID, [{"word": "PCOS", "start_ms": 0, "end_ms": 400, "confidence": 0.9}]
stt._recognize = fake_recognize
rec = stt.transcribe(wav, contract)
assert rec["text"] == SAID and rec["duration_s"] == 3.0 and len(calls) == 1 and calls[0][1] == 16000
assert "메트포르민" in calls[0][2] and "PCOS 여성" in calls[0][2], "contract vocabulary must be boosted"
assert stt.transcribe(wav, contract) == rec and len(calls) == 1, "same audio must replay from cache"
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
print("stt:", note["doc_id"], rec["duration_s"], "s ·", len(rec["keyterms"]), "keyterms · sense on transcript", st2)
print("SELFTEST OK")
