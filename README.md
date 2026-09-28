# nim-evidence-loop

의료진 면담 기록을 환자군 × 신호 유형으로 세고, 문턱을 넘은 조합을 공개 근거(PubMed · ClinicalTrials.gov · openFDA · CMS Part D)로 검증하고,
사람이 서명한 뒤 임원 에이전트 7인이 심의하며, 사람이 결정한 후속 질문만 다음 면담 체크리스트에 넣는 시스템. NVIDIA Nemotron 3 Ultra(NIM)로 동작한다.
모델은 발언을 고르고 인용만 하며, 계수·인용 검증·입장 집계는 코드가 한다.

> Evidence-gated hypothesis loop on NVIDIA Nemotron: field notes → structured claims with verbatim evidence
> pointers → threshold-generated hypotheses → cross-check against PubMed / ClinicalTrials.gov / openFDA / CMS Part D
> → a named human signs → a seven-executive AI board deliberates (opening → discussion → final positions, tallied in code)
> → a named human decides → questions land in the next field checklist.

## 처리 순서

```
면담 기록(한국어 · 녹음 · 현장 수집)   ──sense──▶  발언 카드(원문 위치 필수)  ──코드 집계──▶  임계값 넘은 (환자군 × 신호) → 가설 DRAFT
                                                                                   │
  다음 면담 체크리스트 ◀──approve(사람)── AI Board(간사+임원 7 · 약 20턴) ◀──board── 서명(사람) ◀──review── screen ◀┘
                                          입장 집계·인용 검증 = 코드                                4 공개 근거원 · 인용 검증 · 코드 집계
```

## 지키는 규칙 (프롬프트가 아니라 코드가 강제)

| # | 규칙 | 어디서 |
|---|---|---|
| 1 | **숫자는 모델이 세지 않는다.** 언급 수·의료진 수·지지/반대 건수·시험 수·청구 건수는 전부 코드 | `sense.tally`, `screen.run`, `sources/` |
| 2 | **모든 인용에는 원문 위치가 있다.** 모델이 준 인용문을 원문에서 못 찾으면 «버림»으로 표시하고 세지 않는다 | `quotes.locate` |
| 3 | **사람이 승인하는 것은 가설과 실행이지 발언 카드가 아니다.** 관문은 둘 — 근거 검토 서명, 심의 결정 | `board.sign_review`, `board.approve` |
| 4 | **허가 범위 밖(DEVELOPMENT) 가설은 전문조직 검토로만 간다.** 상업 액션과 연결하지 않는다 | `board.deliberate` |
| 5 | **유해사례 후보는 별도 경로.** 분석 집계에 들어가지 않는다 | `sense.run` → `safety_queue` |
| 6 | **근거가 없으면 순위를 매기지 않는다.** `NO_EXTERNAL_EVIDENCE`는 사람이 읽을 플래그다 | `screen.run` |
| 7 | 화면의 모든 줄은 5단계 중 하나다: **[사실] [패턴] [해석] [제안] [실행]** | `cli.py` |
| 8 | **심의의 권고는 코드가 정한다.** 임원 7인의 최종 입장을 확신 가중(주무 임원 ×1.5)으로 집계해 권고로 옮기고, 간사는 회의록만 쓴다. 발언의 인용은 근거 표에 있는 ID만 인정하며, 허가 밖 가설에 붙은 상업 액션은 차단해 기록에 남긴다 | `board.py` |

## 심의 — AI Board

`board.deliberate`는 원본 설계의 임원 회의를 그대로 따른다.

| 단계 | 누가 | 무엇 |
|---|---|---|
| CONVENE | 간사 | 가설 유형 분류, 주무 임원과 발언 순서 지정, 개회 발언(상정 사실만) |
| OPENING | 임원 7인 (병렬) | 각자의 렌즈로 초기 입장(SUPPORT / HOLD / OPPOSE), 확신 1–5, 근거 인용, 질문·액션 제안 |
| FACILITATE + 답변 | 간사 → 지명된 2~3인 | 서로 다르게 보는 참석자에게 질문. 최대 2라운드, 간사가 진행 여부 결정 |
| FINAL | 임원 7인 (병렬) | 최종 입장. 입장 변경 여부는 모두발언과 비교해 코드가 계산 |
| TALLY | 코드 | 인원수와 확신 가중치(주무 ×1.5)를 세고 가중치가 큰 입장을 권고로 옮긴다. 동률은 HOLD |
| CLOSE | 간사 (사고 모드) | 회의록: 요약 · 근거 요약 · 권고 사유 · 중단 기준 · 위험 · 다음 면담 질문 3개 이내 |

임원: CMO · RA 총괄 · PV 총괄 · R&D 총괄 · CFO · CCO · CEO. 렌즈는 `board.PERSONAS`, 발화 규칙(결론 첫 문장 · 완결 존댓말 · 직함 호칭 · 코드 대신 한국어)은 `prompts/board_persona.md`. 한 번의 심의는 약 20회 호출이며 병렬로 약 5분이 걸린다.

## NVIDIA 스택

- **음성 전사**: `nvidia/parakeet-1.1b-rnnt-multilingual-asr` (기본 · 25개 언어 · ko-KR · 스트리밍) — build.nvidia.com 호스팅 Riva gRPC(`grpc.nvcf.nvidia.com`). 호출은 `loop/stt.py` 한 곳. 같은 음성은 해시로 캐시에서 재생한다. 비교용으로 `--engine whisper`(`openai/whisper-large-v3`, 언어 `ko` · 오프라인 · 60초 단위로 조용한 지점에서 나눠 보냄). build.nvidia.com의 `nemotron-asr-streaming`은 영어 전용이라 쓰지 않는다. function-id는 코드 기본값이 있어 **키만 있으면 된다**.
  - **단어 가중은 기본으로 끈다** (`STT_BOOST` 기본 0, 켜려면 0보다 큰 값 · parakeet만). 깨끗한 합성 음성에서 실측한 CER이 가중 0 → 4.4%, 2 → 8.8%, 5 → 51%, 10 → 109%였다(가중한 낱말을 되풀이해 적는다). 더 나쁜 것은 가중 2에서도 말한 «당뇨 적응증»을 환자군 이름 «당뇨 전단계»로 적었다는 점이다 — 환자군 이름을 가중하면 현장에 없던 신호를 만들고, 집계가 그것을 센다. `data/contract.json`의 `stt_keyterms`는 남아 있지만 기본으로는 쓰지 않는다. `STT_BOOST`가 0 이상의 숫자가 아니면 조용히 0으로 읽지 않는다 — CLI는 이유를 알리고 멈추며, 현장 수집 화면은 이유를 보여 주고 듣기 버튼을 뺀다.
- **합성 음성 (현장 수집 데모)**: `resembleai/chatterbox-multilingual-tts` — 같은 gRPC 호스트, function-id 기본값 `ddacc747-1269-4fab-bfd9-8f593dead106`(`TTS_FUNCTION_ID`로 덮어씀), 한국어 음성은 하나(`Chatterbox-Multilingual.ko-KR.Male`), 24 kHz. 호출은 `loop/tts.py` 한 곳. 함수가 알리는 입력 한도는 500자지만 실제로 막히는 것은 요청 한 번에 약 20초(음성 토큰 500개)라서, 글을 문장 경계에서 110자 이하 조각으로 나눠 합성하고 짧은 무음으로 잇는다. 같은 글도 매번 다른 음성이 나오므로(비결정적) 글의 해시로 캐시한다.
- **추론**: `nvidia/nemotron-3-ultra-550b-a55b` — NIM OpenAI 호환 API, 한국어 공식 지원. 호출은 `loop/llm.py` 한 곳. 구조화 출력(JSON schema)만 받고, 같은 입력은 캐시에서 재생한다.
- **스킬**: `skills/evidence-loop/SKILL.md` — Agent Skills 규격. Claude Code·OpenClaw 등 호환 에이전트에 설치하면 이 루프를 도구로 쓴다.
- **샌드박스**: `sandbox/EGRESS.md` — OpenShell/NemoClaw의 deny-by-default 정책에 넣을 허용 호스트 6개(선택 1개 별도 — 수집 화면의 «GitHub 원본과 대조»). 이 루프의 외부 통신은 그게 전부다.

## 콘솔 (Next.js) — `console/`

원본 DELPHi 콘솔을 그대로 옮겼다. 홈 · 신호의 여정 · 신호와 가설 · 다중 에이전트 검증 · AI Board 회의실 · 안전 · 실행 기록.
백엔드가 아직 제공하지 않는 화면(계약 · 배치 판독 · 시뮬레이터 · 시장 · 수집 지도)은 메뉴에서만 숨겼다 (`console/app/nav.tsx`의 `HIDDEN`).
원본의 «Field 수집분 검토»(`/review/field`)도 숨긴 채로 둔다 — 그 화면의 일은 발언 카드마다 사람이 승인·반려하는 것(`PATCH /claims/{id}`)인데, 이 루프에서 사람이 승인하는 것은 가설과 실행이지 발언 카드가 아니다(규칙 3). 현장 수집은 FastAPI 콘솔의 «현장 수집»(`/collect`)에 있고, 수집한 면담과 새 가설은 기존 호환 API(`/api/hypotheses`, `/api/aggregates/kpis` 등)가 `data/field_notes.json`·`state.json`을 그대로 읽어 Next.js 콘솔에도 보인다. 콘솔 코드는 바꾸지 않았다.
콘솔이 부르는 API는 `loop/compat.py`(회의실 · 결정 · 액션), `loop/compat_hyp.py`(가설 카드 · Screen · 안전 · 실행 기록), `loop/compat_home.py`(홈 · 여정 집계)가
원본 API 계약(`docs/04_API_SPEC.md`의 경로와 응답 형태) 그대로 응답한다.

```bash
cd console && npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8030/api npm run dev -- --port 3010   # 백엔드(8030)가 떠 있어야 한다
```

배포: Railway 서비스 `delphi-console`(Nixpacks, `npm run build` / `node server.js`), 변수 `NEXT_PUBLIC_API_BASE_URL=https://<api>/api`.
`server.js`는 Next 응답을 버퍼링해 `Content-Length`로 내보낸다 — Railway 엣지가 chunked 응답의 종료 청크를 떨어뜨려 Chrome이 홈을 거부하던 문제(원본 팀 09/02 미해결)를 이렇게 돌아갔다. 전 라우트는 요청 시 렌더(`app/layout.tsx`의 `force-dynamic`)이고 루트 `loading.tsx`는 비활성.

## 실행

```bash
uv sync
cp .env.example .env            # build.nvidia.com 에서 발급한 nvapi- 키 (무료)
                                # 선택 설정은 맨 앞 «# » 만 지워 켠다. 같은 줄 뒤의 « # …» 는 dotenv 처럼 주석으로 버린다

# 웹 콘솔 — 리포트 위에 실행 버튼. 단계 순서·관문은 코드가 지킨다
uv run uvicorn loop.web:app --port 8030      # http://localhost:8030

# 같은 것을 CLI로
uv run python -m loop.cli stt-models                # STT 함수가 ko-KR 을 서비스하는지 확인 (--engine whisper 로 비교)
uv run python -m loop.cli transcribe 면담.m4a --hcp HCP-20 --specialty "산부인과 · 개원의" \
       --date 2026-09-28 --consent-by 이름            # 음성 → 전사 → data/field_notes.json 에 한 건 추가 (--no-save: 전사만 · --engine whisper)
uv run python -m loop.cli scripts                   # 현장 수집 대본 목록 — 의도와 지금 집계
uv run python -m loop.cli collect FS-01 --consent-by 이름   # 대본 → TTS → STT(CER) → 면담 기록 한 건 (--no-save · --engine whisper)
uv run python -m loop.cli uncollect                 # 대본에서 수집한 것만 되돌린다
uv run python -m loop.cli tts-voices                # TTS 함수의 언어·음성 (ko-KR 확인)
uv run python -m loop.cli sense                     # 면담 12건 → 발언 카드 → 가설
uv run python -m loop.cli screen HYP-001            # 공개 근거 교차검증
uv run python -m loop.cli review HYP-001 --by 이름   # 관문 ①
uv run python -m loop.cli board HYP-001             # 심의 (Nemotron 사고 모드)
uv run python -m loop.cli approve HYP-001 --by 이름  # 관문 ② → data/field_checklist.json
bash scripts/demo.sh                                # 한 바퀴 전체 · bash scripts/reset.sh 로 결과만 초기화
uv run python scripts/report.py                     # docs/report.html 정적 리포트

# 점검
env -u NVIDIA_API_KEY uv run python scripts/selftest.py   # 오프라인 자체 점검 (키 없이 · 모델 호출 없음)
uv run python scripts/collect_smoke.py              # 실제 호출: TTS 음성 확인 · 대본마다 TTS → MP3 → Parakeet CER (임시 폴더)
uv run python scripts/collect_smoke.py --live FS-01 # 실제 속도 스트리밍 — 첫 중간 결과·확정 지연
uv run python scripts/collect_smoke.py --bake       # 커밋할 재생 세트를 다시 굽는다 (data/field_scripts/ · data/llm_cache/)
bash scripts/collect_ui_check.sh                    # 브라우저(Playwright)로 현장 수집 한 바퀴 — 키 없는 임시 사본에서
```

공개 API 응답(`data/cache/`)과 모델 응답(`data/llm_cache/`)이 저장소에 들어 있어 **키 없이도 같은 입력은 즉시 재생**된다. 새 입력(새 서명자 이름으로 심의 등)만 실제 호출이다.

배포: `Dockerfile` 하나 (`uv sync` → `uvicorn loop.web:app`). 환경변수 `NVIDIA_API_KEY`만 있으면 된다. 상태 파일은 컨테이너 안에 있으므로 재배포하면 결과가 초기화된다 — 데모 용도로는 그게 맞다.

## 현장 수집 — 대본 → TTS → 듣기 → STT → 면담 기록

콘솔 메뉴 **현장 수집**(`/collect`)은 이 루프의 입구를 데모로 보여준다. 의학부 담당자가 면담을 녹음해 오는 자리를, 저장소의 합성 면담 대본으로 대신한다.

1. **대본 (GitHub)** — `data/field_scripts/FS-NN.json` 한 파일에 대본 하나. 목록은 저장소 사본에서 읽고, 화면은 GitHub 저장소·브랜치·커밋과 파일마다의 GitHub 링크를 보여준다(«GitHub 원본과 대조»는 해시만 비교하고, 받아 온 내용은 쓰지 않는다). 가상 의료진 HCP-13~16, 실제 인물·기관 없음. 대본의 `intent`(어느 환자군 × 신호에 보태려고 썼는지)는 화면에만 보이고, 면담 기록에도 모델 입력에도 들어가지 않는다.
2. **음성 (TTS)** — Chatterbox Multilingual이 읽은 MP3 한 개. 평가자가 듣는 파일과 STT가 듣는 파일이 **같은 파일**이고, 그 sha256이 화면과 면담 기록의 출처(`stt.audio_sha256`)에 남는다.
3. **듣기 · 받아 적기 (STT)** — «▶ 재생하며 받아 적기»를 누르면 음성이 나오고, 같은 순간부터 서버가 같은 MP3를 16 kHz로 풀어 Parakeet에 **실제 속도로** 흘려 보낸다. 중간 결과(회색)와 확정된 글(검정)이 화면에 차례로 올라온다(실측: 누른 뒤 약 1.8초에 첫 글자, 음성이 끝나고 약 0.2초 뒤 확정). 끝나면 대본 대비 글자 오류율(CER, 띄어쓰기·문장부호 제외)을 코드가 계산하고, 다르게 받아 적은 곳을 표시한다. STT 요청에는 마감 시각이 있다(음성 길이 + 20초). 실시간 듣기 5번 중 1번은 NVCF 스트림이 끝 무렵 응답을 멈추고 닫히지 않았다 — 마감이 지나면 서버가 끊고 «STT 응답이 멈췄습니다 — 다시 누르면 처음부터 듣습니다»를 보여 주며, 버튼이 다시 켜지고 다른 대본 듣기도 막히지 않는다. 누가 듣는 중이면 처음부터 다시 흘려 보내지 않는다(브라우저의 음성은 이미 지나갔다).
4. **면담 기록 (동의)** — 녹음 동의를 확인한 사람의 이름을 적어야 저장된다. 저장되는 글은 화면에 받아 적힌 글 그대로이고(틀린 글자도 고치지 않는다), 의료진·전문과·날짜는 대본에 고정된 값이다. 같은 대본·같은 음성은 두 번 들어가지 않는다.
5. **추출 → 가설** — «다음 단계 — ① 추출 실행»은 개요의 ① 추출과 **같은 함수**다. «이번 수집이 바꾼 것»은 수집한 면담의 발언 카드를 빼고 센 집계와 넣고 센 집계를 코드로 나란히 보인다. 새 가설부터는 이미 있는 흐름(② 근거 교차검증 → ③ 서명 → ④ 심의 → ⑤ 결정 → 체크리스트)이 그대로 이어진다.

«수집 되돌리기»는 다음 평가자를 위해 대본에서 수집한 면담 기록과, 그로부터 생긴 발언 카드·safety 항목·가설 초안만 지운다(면담 기록 파일은 수집 전과 바이트 단위로 같아진다). 그 가설에 사람의 서명이 있으면 되돌리지 않는다.
가설 번호는 차례대로 붙는다. 한 번의 추출이 수집 가설과 원래 면담의 가설을 함께 만들었으면 — 개요 «초기화» 뒤 FS-01 → ① 추출은 노인 × 용량(7회/3인)을 HYP-001, PCOS(6회/4인)를 HYP-002로 만든다 — 수집 가설을 지우고 원래 면담의 가설은 번호만 당긴다(HYP-002 → HYP-001). 초안을 만드는 입력(집계 한 줄과 그 인용)에는 번호가 없으므로 수집 없이 추출한 결과와 똑같고, 모델 호출도 없다. 당겨야 할 가설이 이미 근거 교차검증·서명·심의를 거쳤으면 그 기록에 번호가 남아 있어 되돌리지 않고, 이유와 함께 개요의 «초기화» → «수집 되돌리기» 한 번 더를 안내한다(초기화는 결과만 지우고, 수집한 면담 기록은 «수집 되돌리기»가 지운다). 근거 교차검증이 돌고 있는 동안에는 기다리라고 한다.

**구워 둔 결과** (2026-09-28 · `data/field_scripts/bake.json`) — Chatterbox → Parakeet(단어 가중 없음) 한 번씩, 추출·가설 초안은 Nemotron 3 Ultra.

| 대본 | 의료진 · 전문과 | 음성 | CER | 의도 | 이 대본이 바꾼 것 (코드 집계) |
|---|---|---|---|---|---|
| FS-01 요양병원 어르신 — 신장 기능에 맞춘 감량 | HCP-13 · 노년내과 | 25.7초 | 7.7% | 노인 65+ · 신기능 저하 × DOSING | 발언 4장이 모두 DOSING → **3회/2인 → 7회/3인, 문턱 통과 → 새 가설 초안** (커밋된 상태에서 HYP-006) |
| FS-02 임신성 당뇨 — 먹는 약을 원하는 산모 | HCP-14 · 산부인과 | 19.3초 | 5.1% | 임신부 · 임신성 당뇨 × OFF_LABEL_DEMAND | 쓰고 싶은데 막혔다 2장 + 충족되지 않은 필요 1장 |
| FS-03 임신성 당뇨 협진 — 허가의 벽 | HCP-15 · 내분비내과 | 20.9초 | 3.1% | 같은 묶음 | 발언 1장을 **버림** — STT가 «허가 밖 처방»을 «허가 밥 처방»으로 적었는데 모델이 인용하면서 «밖»으로 고쳐, 원문과 달라졌다. 코드가 원문에서 못 찾았으니 세지 않는다(규칙 2) |
| FS-04 청소년 — 첫 주 위장관 반응 | HCP-16 · 소아청소년과 | 18.7초 | 2.6% | 청소년 10-17세 × DOSING | DOSING 2장(0 → 2회/1인), 응급실 방문은 **유해사례 후보 → safety 큐**(2 → 3건, 집계에 넣지 않음) |

네 건을 모두 수집하면 노인 × 용량 3회/2인 → 7회/3인(가설), 임신부 × 쓰고 싶은데 막혔다 1회/1인 → 3회/2인(임계 근접 — 언급 2회·의료진 1인 모자람), 임신부 × 충족되지 않은 필요 0 → 1회/1인, 청소년 × 용량 0 → 2회/1인, 발언 카드 +10(버림 1), 유해사례 후보 +1. 커밋된 상태에서도 초기화한 상태에서도 숫자는 같다. 가설 번호만 다르다 — 커밋된 상태에서는 HYP-006, 초기화 뒤에는 추출 순서에 따라 정해진다.

**키 없이 재생되는 것**: 목록 → 음성(구운 MP3) → 듣기(구운 전사를 낱말 시각에 맞춰 보여주고, 화면에 «모델 호출 없음»이라고 밝힌다) → 저장 → ① 추출(발언 카드·가설 초안이 `data/llm_cache/`에서) → 새 가설 초안. **키가 필요한 것**: 실시간 STT(같은 음성도 새로 듣는다), 음성 다시 만들기, 새로 받아 적은 글의 추출(약 1분). **공개 근거원 네트워크가 필요한 것**: 새 가설의 ② 근거 교차검증 — 구워 두지 않았다(이 개발 환경에서는 PubMed·CT.gov·openFDA·CMS가 프록시에 막혀 있었다).

**TTS로 읽힐 대본을 쓰는 법 (실측)** — 라틴 문자를 쓰지 않는다(«PCOS» → «피고», «eGFR» → «에게펄»). 숫자는 한국어로 읽는 대로 쓴다(«1,000mg»은 «천억»으로 돌아왔고 «천 밀리그램»은 그대로 돌아온다). 음성이 뭉개는 낱말은 피한다(«콩팥» → «콤바», «제2형» → «대도형», «신장»은 안전). 대본 첫머리에 중요한 낱말을 두지 않는다 — FS-04가 처음 «당뇨로 진단된 …»으로 시작했을 때 Parakeet은 음성 앞 무음을 0.4초에서 2초로 늘려도 «당뇨로»를 놓쳤다. 문장은 60~110자, 대본은 150~230자(약 18~26초).

## 데모 각본 — 메트포르민 (2026-09-28 실측)

**모든 면담 기록은 합성이다** (`data/field_notes.json`, 가상 의료진 12인). 약은 특허가 만료된 지 오래고 특정 회사 소유가 아니며 공개 근거가 가장 두꺼워서 골랐다(PubMed 제목 18,054편 · CT.gov 3,120건 · FAERS 440,270건 · Part D 연 3,400만 건 청구).

**Sense**: 면담 12건 → 발언 카드 30장, **원문 검증 통과 30/30**, 유해사례 후보 2건은 safety 큐로. 문턱(3회/3인)을 넘은 묶음 5개가 가설이 됐다.

| 가설 | 현장 | 외부 근거 (지지/반대/중립 · 버림) | 이 장면이 보여주는 것 |
|---|---|---|---|
| HYP-003 유방암 환자 × 쓰고 싶다(OFF_LABEL_DEMAND) | 3회/3인 | 10 / **3** / 19 · 2 | **반대 근거 장면.** 언론 보도로 환자 요청은 늘지만, 대규모 3상 MA.32(NCT01101438, n=3,649)는 무효. 같은 시험이 CT.gov에서는 «진지하게 시험됐다»(지지), PubMed 결과에서는 반대로 잡힌다 — 등록과 결과는 다르다. 심의(사고 모드) 권고 **DROP**, 근거 첫 줄이 MA.32 무효 결과, 후속 질문 3개가 체크리스트로. 같은 근거로 실행에 따라 HOLD가 나온 적도 있다 — 둘 다 방어 가능하고, 결정은 어차피 사람이 한다 |
| HYP-001 PCOS 여성 × 써봤다(OFF_LABEL_USE) | 6회/4인 | 9 / 0 / 27 · 3 | 지지 근거 장면. 허가 밖이지만 3상 42건 — DEVELOPMENT 경로로 전문조직 검토 |
| HYP-002 당뇨 전단계 × 막혔다(OFF_LABEL_DEMAND) | 4회/4인 | 23 / 2 / 13 · 1 | 라벨 경계 장면. 라벨은 침묵(중립), 시험은 117건 — «막혔다»와 «써봤다»를 가르는 이유 |
| HYP-004 유방암 × 다른 쓰임(REPURPOSING) | 3회/3인 | 9 / 1 / 7 · 3 | 당뇨 동반 환자의 관찰 연구 — 3상 결과 논문(PMID:35608580)이 함께 읽힌다 |
| HYP-005 유방암 × 자료 부족(UNMET_NEED) | 3회/3인 | 2 / 2 / 7 · 1 | 근거가 얇으면 얇다고 보인다 |
| 소아 10세 미만 · 노인 신기능 · 임신부 | 1~2인 | — | 임계 미달. 집계에는 보이지만 가설이 되지 않는다 — 문턱은 코드다 |
| 젖산산증 입원 · B12 결핍 신경병증 | 2건 | — | safety 큐. 분석에 섞이지 않는다 |

«버림»은 모델이 지어낸 인용이 아니라 **문장 중간을 건너뛰어 이어 붙인 것**들이다 — 그래도 세지 않는다. 공백·기호 차이(라벨 원문의 `( 5.1 )`, `Vitamin B 12`)와 «…»로 나뉜 조각은 코드가 원문에서 순서대로 찾아 살린다.

근거 읽기는 **RCT·3상·메타분석을 먼저** 읽는다(`screen.gather`). 관련도순 상위만 읽으면 가장 큰 시험이 밀린다 — MA.32가 처음엔 안 잡혔던 이유.

## 안 만든 것 · 한계
- 현장 수집 모바일 앱은 이 저장소에 없다. **음성 전사와 수집 데모는 있다** — `transcribe` 명령과 면담 기록 페이지(`/notes`)의 업로드가 녹음 파일을 Parakeet 다국어(또는 Whisper)로 전사해 `data/field_notes.json`에 넣고, 현장 수집(`/collect`)은 합성 대본을 TTS로 읽혀 같은 길로 넣는다. 데모의 원래 12건은 여전히 합성 텍스트다.
  - 현장 수집 데모의 한계: 마이크 입력은 없다(서버가 재생 중인 파일을 STT에 흘려 보낸다). 플레이어를 멈추거나 옮겨도 STT는 멈추지 않는다. 브라우저와 STT의 시작 시각은 0.1초 안팎 어긋날 수 있다. 서버 하나에서 한 번에 한 대본만 듣는다(멈춘 듣기는 마감 뒤 이 자리를 내준다). 새 가설의 근거 교차검증은 공개 근거원에 닿는 배포 환경에서만 된다.
  - 녹음 동의를 확인한 사람의 이름이 없으면 기록이 되지 않는다(`stt.add_note`). 같은 녹음은 두 번 들어가지 않는다(해시). 음성 파일은 저장하지 않는다.
  - 전사 출처(모델 · 해시 · 동의자)는 `stt` 칸에 남고 추출 모델에는 보내지 않는다 — 기존 12건의 캐시 키가 그대로라 키 없이 재생된다.
  - 이번 범위 밖: 실시간 스트리밍 입력(마이크), 화자 분리, 개인식별정보 자동 가림. 실제 면담 녹음을 넣으려면 가림 단계가 먼저 필요하다.
  - 실제 호출 점검: `uv run python scripts/stt_smoke.py` (키 · LLM · 엔진별 서비스 언어) · `--audio 녹음.m4a --ref-note FN-…` 로 두 엔진 전사와 글자 오류율(CER) 비교.
  - 2026-09-28 실호출 확인: 키 하나로 두 엔진 모두 응답, parakeet은 ko-KR(스트리밍·오프라인 모델 둘 다), Whisper는 `ko` 서비스. 사람 음성(Riva 예제)은 parakeet이 정확히 받아 적었고, espeak-ng 기계음은 parakeet의 음성 구간 검출(VAD)이 말소리로 보지 않아 빈 결과 — 합성 음성으로 시험하려면 자연스러운 TTS가 필요하다. Whisper는 기계음도 받아 적지만 CER 44~76%.
  - 한국어 의료 대화 정확도(사람 녹음)는 아직 실측하지 않았다. `data/stt_cache/`는 개인정보라 커밋하지 않는다(`.gitignore`).
- OpenShell 안에서 실행해 보지 않았다 (개발 환경이 macOS). 정책은 `sandbox/EGRESS.md`.
- `data/state.json` 하나가 정본이라 **명령은 한 번에 하나씩** 돌린다. 동시에 돌리면 나중 저장이 앞 저장을 덮는다.
- 모델 호출 1회 ≈ 20~60초(Nemotron 3 Ultra, 무료 엔드포인트). 한 바퀴 약 27회. 같은 입력은 캐시에서 즉시 재생된다.
