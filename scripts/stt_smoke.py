"""Live check of the STT entrance against build.nvidia.com — the part selftest.py fakes.

    uv run python scripts/stt_smoke.py                                   # key · network · which languages each engine serves
    uv run python scripts/stt_smoke.py --audio 녹음.m4a --ref-note FN-2026-0702-01   # + transcribe with both engines, CER
    uv run python scripts/stt_smoke.py --audio 녹음.m4a --ref-text 정답.txt

Recording tip: read one synthetic note from data/field_notes.json aloud — its text is then the reference, and
CER (character error rate, spaces and punctuation ignored) says how far each engine is from it.
Nothing is written to data/field_notes.json. Transcripts are cached in data/stt_cache/ like any transcription.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loop import llm, store, stt  # noqa: E402


def cer(hyp: str, ref: str) -> float:
    norm = lambda s: re.sub(r"[\s\W_]+", "", s.lower())   # noqa: E731
    h, r = norm(hyp), norm(ref)
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i]
        for j, hc in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1] / max(len(r), 1)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--audio", type=Path)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--ref-note", help="doc_id in data/field_notes.json whose text was read aloud")
    g.add_argument("--ref-text", type=Path)
    p.add_argument("--engine", choices=list(stt.ENGINES), action="append", help="default: all engines")
    a = p.parse_args()
    engines = a.engine or list(stt.ENGINES)
    ok = True

    try:
        key = llm.api_key()
        print(f"[OK]   NVIDIA_API_KEY nvapi-…{key[-3:]}")
    except llm.LlmUnavailable as e:
        print(f"[FAIL] {e}")
        return 1

    import httpx
    try:
        r = httpx.get(f"{llm.BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        print(f"[{'OK' if r.status_code == 200 else 'FAIL'}]   LLM  {llm.BASE_URL} → HTTP {r.status_code}")
        ok &= r.status_code == 200
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] LLM  {llm.BASE_URL} → {type(e).__name__}: {str(e)[:160]}")
        ok = False

    for eng in engines:
        cfg = stt.settings(eng)
        try:
            langs = stt.list_models(eng)
            want = cfg["language"]
            has = want in langs or want == "multi"
            print(f"[{'OK' if has else 'FAIL'}]   STT  {eng:<8} {cfg['model']} (function-id {cfg['function_id']}) — "
                  f"언어 {len(langs)}개{', ko-KR 있음' if 'ko-KR' in langs else ''}: {', '.join(langs)[:200]}")
            ok &= has
        except Exception as e:  # noqa: BLE001 — grpc errors carry the useful part in str(e)
            print(f"[FAIL] STT  {eng:<8} {cfg['model']} → {type(e).__name__}: {str(e)[:300]}")
            ok = False

    if a.audio:
        ref = None
        if a.ref_note:
            ref = next((n["text"] for n in json.loads(store.FIELD_NOTES.read_text()) if n["doc_id"] == a.ref_note), None)
            if ref is None:
                print(f"[FAIL] 면담 기록에 없는 doc_id: {a.ref_note}")
                return 1
        elif a.ref_text:
            ref = a.ref_text.read_text()
        for eng in engines:
            try:
                rec = stt.transcribe(a.audio, store.contract(), engine=eng)
                score = f" · CER {cer(rec['text'], ref):.1%}" if ref else ""
                print(f"[OK]   전사 {eng:<8} {rec['duration_s']}초 → {len(rec['text'])}자{score}\n       “{rec['text']}”")
            except Exception as e:  # noqa: BLE001
                print(f"[FAIL] 전사 {eng:<8} → {type(e).__name__}: {str(e)[:300]}")
                ok = False
        if ref:
            print(f"       정답 “{ref}”")
    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
