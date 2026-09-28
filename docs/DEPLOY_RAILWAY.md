# 내 Railway에 배포하기 — 백엔드 1개 + 콘솔 1개

이 문서는 **사용자 저장소 `jih675955555-design/delphi-evidence-loop`** 를 **사용자 본인의 Railway 계정**에 올리는 절차다.
원래 저장소(`coldtype-08/delphi-evidence-loop`)와 그 Railway 프로젝트는 **읽지도 쓰지도 않는다** — 여기서 만드는 것은 전부 새 프로젝트다.

| 서비스 | 소스 | 빌드 | 필요한 변수 |
|---|---|---|---|
| `delphi-web` (백엔드 · FastAPI) | 저장소 루트 `/` | 루트의 `Dockerfile` (자동 인식) | `NVIDIA_API_KEY` · (권장) `DELPHI_CONSOLE_URL` |
| `delphi-console` (Next.js 콘솔) | Root Directory `console` | `console/railway.json` (Nixpacks · `npm run build` · `node server.js`) | `NEXT_PUBLIC_API_BASE_URL` |

- 브랜치: 지금은 **`claude/funny-babbage-3aqyhn`** (수집 단계가 있는 브랜치). 이 브랜치에 푸시할 때마다 Railway가 다시 배포한다. 나중에 `main` 으로 합치면 두 서비스의 브랜치를 `main` 으로 바꾼다.
- `PORT` 는 Railway가 넣어 준다. 두 서비스 모두 `$PORT` 를 읽는다(`Dockerfile` CMD · `console/server.js`). 따로 넣지 않는다.
- 백엔드 CORS는 모든 출처를 허용한다(`loop/web.py`) — 콘솔 도메인을 따로 등록할 필요는 없다.

## A. 대시보드로 (약 5분)

1. [railway.com](https://railway.com) 에 **본인 GitHub 계정으로 로그인**한다.
   처음이면 Railway GitHub 앱이 이 저장소를 읽을 수 있게 허용한다(비공개 저장소면 *Only select repositories* 에 `delphi-evidence-loop` 를 고른다).
2. **New Project → Deploy from GitHub repo → `jih675955555-design/delphi-evidence-loop`**.
   만들어진 서비스가 백엔드다. 이름을 `delphi-web` 으로 바꾼다.
3. `delphi-web` → **Settings**
   - *Source* → Branch: `claude/funny-babbage-3aqyhn`
   - *Build*: Dockerfile 이 자동으로 잡힌다(Builder = Dockerfile). Root Directory 는 비워 둔다.
   - *Deploy* → Healthcheck Path: `/health` (선택)
   - *Networking* → **Generate Domain** → 예: `https://delphi-web-xxxx.up.railway.app` (이 주소를 적어 둔다)
4. `delphi-web` → **Variables**
   - `NVIDIA_API_KEY` = `nvapi-…` (build.nvidia.com 에서 새로 발급한 키. 채팅이나 저장소에 붙이지 않는다)
   - `DELPHI_CONSOLE_URL` = 5단계에서 만들 콘솔 주소 (소개 페이지의 «콘솔 열기» 링크가 원래 저장소의 배포가 아니라 **내 콘솔**을 가리키게 한다. 없으면 원래 주인의 콘솔로 간다)
5. 같은 프로젝트에서 **+ New → GitHub Repo → 같은 저장소**로 서비스를 하나 더 만든다. 이름 `delphi-console`.
   - *Settings → Source* → Branch: `claude/funny-babbage-3aqyhn` · **Root Directory: `console`**
   - *Variables* → `NEXT_PUBLIC_API_BASE_URL` = `https://<3단계 백엔드 도메인>/api`
     (`NEXT_PUBLIC_` 변수는 **빌드할 때** 박힌다 — 값을 바꾸면 반드시 Redeploy)
   - *Networking* → **Generate Domain** → 예: `https://delphi-console-xxxx.up.railway.app`
6. 4단계의 `DELPHI_CONSOLE_URL` 을 6단계 콘솔 주소로 채우고 `delphi-web` 을 Redeploy 한다.

### 확인
- `https://<백엔드>/health` → `{"ok": true, …}`
- `https://<백엔드>/collect` → 현장 수집 화면(대본 목록)
- `https://<콘솔>/` → 콘솔 홈. 숫자가 보이면 백엔드와 연결된 것이다. «백엔드에 연결하지 못했습니다»가 나오면 `NEXT_PUBLIC_API_BASE_URL` 을 확인하고 콘솔을 Redeploy 한다.

## B. Claude Code 세션에서 CLI로 (선택)

Claude Code 클라우드 세션이 대신 배포하려면 **세션 환경 설정**(제목줄의 환경 메뉴 → Edit)에 다음이 먼저 있어야 한다. 설정은 새 세션부터 적용된다.

- Network access 허용: `railway.com`, `backboard.railway.com`, `backboard.railway.app`
- 환경 변수: `RAILWAY_API_TOKEN` (railway.com → Account Settings → Tokens 에서 만든 **계정 토큰**), `NVIDIA_API_KEY`
- Railway GitHub 앱이 이 저장소에 설치돼 있어야 한다(A-1). CLI만으로는 이 허용을 줄 수 없다.

```bash
npm i -g @railway/cli
railway whoami                                   # RAILWAY_API_TOKEN 이 먹는지 — 실패하면 토큰/네트워크부터
railway init --name delphi-evidence-loop         # 새 프로젝트 (내 계정)
railway add --service delphi-web --repo jih675955555-design/delphi-evidence-loop \
            --variables "NVIDIA_API_KEY=$NVIDIA_API_KEY"
railway add --service delphi-console --repo jih675955555-design/delphi-evidence-loop
railway domain --service delphi-web              # 백엔드 도메인 → 아래 변수에
railway variables --service delphi-console --set "NEXT_PUBLIC_API_BASE_URL=https://<백엔드 도메인>/api"
railway domain --service delphi-console
railway variables --service delphi-web --set "DELPHI_CONSOLE_URL=https://<콘솔 도메인>"
```

> **이 경로는 아직 실제로 돌려 보지 않았다** (문서를 쓴 개발 환경에서는 railway.com 이 네트워크 정책에 막혀 있었다).
> CLI 버전에 따라 플래그가 다를 수 있으니 각 명령의 `--help` 로 먼저 확인한다.
> 콘솔 서비스의 **브랜치와 Root Directory(`console`)** 는 CLI 플래그가 없으면 대시보드(A-5)에서 한 번 지정한다.

## 알아둘 것

- **상태는 컨테이너 안에 있다.** 재배포하면 `data/state.json` 이 저장소 값으로 돌아간다(수집한 면담·서명·심의 결과도 초기화). 데모 용도로는 그게 맞다 — README «실행» 참고.
- **키가 없어도 뜬다.** `NVIDIA_API_KEY` 가 없으면 저장소에 넣어 둔 음성·전사·모델 응답으로 수집 장면을 재생하고, 화면에 «모델 호출 없음»이라고 밝힌다.
- **외부 호출**은 `sandbox/EGRESS.md` 의 호스트뿐이다(Nemotron · NVCF gRPC · PubMed · CT.gov · openFDA · CMS). Railway는 기본으로 모두 나갈 수 있다.
- 원래 저장소의 배포(`delphi-web-production-52d6…`, `delphi-console-production-8ae8…`)는 그대로 둔다. 이 문서는 그것을 바꾸지 않는다.
