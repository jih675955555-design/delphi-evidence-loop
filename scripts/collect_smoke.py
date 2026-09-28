"""Live check of the collection stage against build.nvidia.com — the part selftest.py replays from the bake.

    uv run python scripts/collect_smoke.py                          # voices · every script: TTS → MP3 → Parakeet, CER
    uv run python scripts/collect_smoke.py --script FS-01 --engine whisper   # + Whisper on the same MP3, for comparison
    uv run python scripts/collect_smoke.py --live FS-01             # paced streaming: first interim latency, final lag
    uv run python scripts/collect_smoke.py --bake                   # write the committed replay set (see below)

Without --bake everything goes to a temp dir — nothing in data/ changes, and TTS/STT are real calls.

--bake writes what lets the whole collection chain replay without a key:
  data/field_scripts/audio/FS-NN.mp3 + .json   the audio the listener and STT hear (TTS WAV cached in data/tts_cache)
  data/field_scripts/stt/{key}.json            Parakeet's transcript of that exact MP3 (word boost off)
  data/llm_cache/*.json                        extraction and hypothesis drafts for those transcripts
  data/field_scripts/bake.json                 manifest: hashes, CER, the STT and LLM cache keys used, the effect
The model steps run on temp copies of (a) data/state.json and (b) an empty state, with the scripts collected into a
temp copy of data/field_notes.json — the real files are never touched. Re-running --bake reuses every cache, so it
is idempotent; delete data/tts_cache/ to synthesise new audio (then the whole chain changes: audio → transcript →
extraction), and commit bake.json with it.
"""
import argparse
import copy
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loop import collect, llm, sense, store, stt, tts  # noqa: E402

MAX_CER = 0.15


def _scratch(tmp: Path) -> None:
    collect.RUNTIME_DIR = tmp / "collect_runtime"
    tts.CACHE_DIR, tts.RUNS_LOG = tmp / "tts_cache", tmp / "tts_runs.jsonl"
    stt.CACHE_DIR, stt.RUNS_LOG = tmp / "stt_cache", tmp / "stt_runs.jsonl"


def roundtrip(s: dict, engines: list[str], baked: bool) -> tuple[bool, dict]:
    t0 = time.time()
    a = collect.make_audio(s, baked=baked)
    print(f"[OK]   TTS  {s['script_id']} {s['chars']}자 → {a['duration_s']}초 · {a['tts']['chunks']}조각 {a['tts']['chunk_chars']} · "
          f"MP3 {a['bytes'] // 1024} KB · {time.time() - t0:.1f}초")
    ok, out = True, {"audio": a}
    for eng in engines:
        t1 = time.time()
        kw = {"cache_dir": collect.SCRIPTS_DIR / "stt"} if baked and eng == "parakeet" else {}
        rec = stt.transcribe(Path(a["path"]), store.contract(), engine=eng, **kw)
        c = stt.cer(rec["text"], s["text"])
        good = bool(rec["text"].strip()) and c <= MAX_CER
        ok &= good
        print(f"[{'OK' if good else 'FAIL'}]   STT  {eng:<8} CER {c:.1%} · {len(rec['text'])}자 · 가중 {rec['boost']:g} · {time.time() - t1:.1f}초\n"
              f"       “{rec['text']}”")
        out[eng] = {"rec": rec, "cer": c}
    return ok, out


def live(s: dict) -> bool:
    a = collect.audio(s) or collect.make_audio(s)
    t0, first, n = time.time(), [None], [0]

    def cb(finals, interim):
        n[0] += 1
        if first[0] is None and (interim or finals):
            first[0] = time.time() - t0
    rec = collect.transcript(s, mode="live", on_partial=cb, pace=1.0)
    lag = time.time() - t0 - a["duration_s"]
    good = first[0] is not None and first[0] <= 3 and lag <= 2
    print(f"[{'OK' if good else 'FAIL'}]   실시간 {s['script_id']} 음성 {a['duration_s']}초 · 첫 중간 결과 {first[0] if first[0] is None else round(first[0], 2)}초 · "
          f"중간 결과 {n[0]}회 · 끝난 뒤 {lag:.2f}초에 확정 · CER {stt.cer(rec['text'], s['text']):.1%}")
    return good


def bake(scripts: list[dict]) -> bool:
    ok = True
    contract = store.contract()
    results = {}
    for s in scripts:
        good, r = roundtrip(s, ["parakeet"], baked=True)
        ok &= good
        results[s["script_id"]] = r
    # stale baked files (a script removed or re-synthesised) would only confuse the replay
    keep_stt = {r["parakeet"]["rec"]["cache_key"] for r in results.values()}
    for p in (collect.SCRIPTS_DIR / "stt").glob("*.json"):
        if p.stem not in keep_stt:
            p.unlink()
    for p in (collect.SCRIPTS_DIR / "audio").glob("*"):
        if p.stem not in results:
            p.unlink()

    keys: set[str] = set()
    real_log = llm._log

    def log(**rec):   # every model call the chain makes, cached or not — the manifest lists them
        if rec.get("key") and "retry" not in rec:
            keys.add(rec["key"])
        real_log(**rec)
    llm._log = log
    tmp = Path(tempfile.mkdtemp())
    collect.RUNTIME_DIR = tmp / "collect_runtime"   # runtime audio must not shadow the baked files
    effects = {}
    for label, start in (("committed_state", json.loads(store.STATE.read_text())), ("fresh_state", copy.deepcopy(store.EMPTY))):
        store.STATE = tmp / f"{label}.json"
        store.STATE.write_text(json.dumps(start, ensure_ascii=False))
        store.FIELD_NOTES = tmp / f"{label}_notes.json"
        shutil.copy(store.ROOT / "data" / "field_notes.json", store.FIELD_NOTES)
        for s in scripts:
            collect.save(s, "베이크", collect.transcript(s, mode="replay"), listen_mode="replay")
        state = store.load()
        notes = json.loads(store.FIELD_NOTES.read_text())
        st = sense.run(state, contract, notes)
        created = sense.draft_hypotheses(state, contract, contract["threshold"]["min_mentions"], contract["threshold"]["min_hcps"])
        eff = collect.effect(state, contract, notes)
        effects[label] = {"sense": st, "created": [{"id": h["id"], "segment": h["segment"], "signal_type": h["signal_type"],
                                                   "mentions": h["field"]["mentions"], "hcps": h["field"]["hcps"]} for h in created],
                          "rows": eff["rows"], "safety_added": eff["safety_added"], "dropped": eff["dropped"],
                          "claims_added": eff["claims_added"]}
        print(f"[사실] {label}: 추출 {st} → 새 가설 {[(h['id'], h['segment'], h['signal_type'], h['field']['mentions'], h['field']['hcps']) for h in created]}")
        for r in eff["rows"]:
            print(f"[패턴]    {r['segment']} × {r['signal_type']}  {r['before'][0]}회/{r['before'][1]}인 → {r['after'][0]}회/{r['after'][1]}인"
                  f"{' · 문턱 통과' if r['passed_after'] else ''}{' → ' + r['hypothesis_id'] if r['hypothesis_id'] else ''}")
        print(f"[사실]    유해사례 후보 +{eff['safety_added']} · 버림 {eff['dropped']} · 발언 카드 +{eff['claims_added']}")
    llm._log = real_log
    missing = [k for k in keys if not (llm.CACHE_DIR / f"{k}.json").exists()]
    ok &= not missing
    manifest = {
        "baked_at": store.now(), "engine": "parakeet", "boost": 0, "tts": {k: tts.ENGINE[k] for k in ("model", "function_id", "voice")},
        "scripts": {sid: {"audio_sha256": r["audio"]["audio_sha256"], "duration_s": r["audio"]["duration_s"],
                          "stt_cache_key": r["parakeet"]["rec"]["cache_key"], "cer": round(r["parakeet"]["cer"], 4),
                          "chars": len(r["parakeet"]["rec"]["text"])} for sid, r in results.items()},
        "llm_keys": sorted(keys), "effect": effects,
    }
    (collect.SCRIPTS_DIR / "bake.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(f"[실행] {collect.SCRIPTS_DIR.relative_to(store.ROOT)}/bake.json — 모델 응답 키 {len(keys)}개" + (f" · 캐시에 없음 {missing}" if missing else ""))
    return ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--script", action="append", help="FS-NN (여러 번 가능) · 기본: 전부")
    p.add_argument("--engine", choices=list(stt.ENGINES), action="append", help="기본 parakeet")
    p.add_argument("--live", metavar="SID", help="실제 속도 스트리밍 — 첫 중간 결과와 확정 지연")
    p.add_argument("--bake", action="store_true", help="커밋할 재생 세트를 만든다 (data/field_scripts/, data/llm_cache/)")
    a = p.parse_args()
    if not collect.can_call_models():
        print("[FAIL] NVIDIA_API_KEY 가 없습니다 — .env 에 넣으세요.")
        return 1
    print("[OK]   NVIDIA_API_KEY 있음")
    ok = True
    try:
        voices = tts.list_voices()
        has = tts.settings()["voice"] in voices.get("ko-KR", [])
        print(f"[{'OK' if has else 'FAIL'}]   TTS  {tts.settings()['model']} — ko-KR 음성 {voices.get('ko-KR')} · 언어 {len(voices)}개")
        ok &= has
    except Exception as e:  # noqa: BLE001 — gRPC errors carry the useful part in str(e)
        print(f"[FAIL] TTS  {type(e).__name__}: {str(e)[:300]}")
        return 1
    scripts = [collect.get(sid) for sid in a.script] if a.script else [s for s in collect.list_scripts() if "_error" not in s]
    if a.bake:
        ok &= bake(scripts)
    else:
        _scratch(Path(tempfile.mkdtemp()))
        if a.live:
            ok &= live(collect.get(a.live))
        else:
            for s in scripts:
                ok &= roundtrip(s, a.engine or ["parakeet"], baked=False)[0]
    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
