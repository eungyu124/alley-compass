# 골목 컴퍼스 API

`alley_compass_etl/`의 검증 로직(fact_sheet.py, narrative_agents.py,
verification_tools.py)을 그대로 재사용하는 FastAPI 백엔드. 새 판정 로직을
여기서 다시 만들지 않는다 — 있는 걸 노출만 한다.

## 실행

```bash
conda activate alleycompass
cd backend
pip install -r requirements.txt   # alleycompass 환경엔 이미 다 있을 것
uvicorn main:app --reload --port 8000
```

기본은 로컬 CSV(`alley_compass_etl/data/processed/district_features_debug.csv`)를
읽는다. Supabase로 전환하려면 `alley_compass_etl/.env`에

```
BACKEND_USE_SUPABASE=true
```

를 추가한다 (단, `db/schema_v1.1.sql`의 v1.2 패치 — `service_role` GRANT —
가 먼저 Supabase에 적용돼 있어야 한다).

http://localhost:8000/docs 에서 Swagger UI로 바로 테스트 가능.

## 배포 (Render)

Vercel(프론트)과 별개로 백엔드는 Render에 Docker로 올린다. Vercel의 기본
Python 서버리스 런타임은 `report.py`가 쓰는 WeasyPrint(Pango/Cairo 같은 OS
라이브러리 필요)를 못 다뤄서, Dockerfile로 apt 패키지까지 직접 설치하는
쪽으로 갔다. 저장소 루트의 `Dockerfile`/`.dockerignore`/`render.yaml`이 이
설정이다(빌드 컨텍스트가 루트인 이유: `alley_compass_etl/`이 `backend/`의
형제 디렉터리라 같이 COPY해야 함).

1. Render 대시보드 → **New +** → **Blueprint** → 이 GitHub 저장소 선택
   (`render.yaml`을 그대로 읽어 서비스를 만든다). 수동으로 **Web Service**를
   만들어도 되는데, 그때는 Runtime을 **Docker**로, Dockerfile 경로를
   `./Dockerfile`로 지정한다.
2. 생성된 서비스의 **Environment** 탭에서 값을 채운다 (`alley_compass_etl/.env`
   와 같은 값):
   - `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `ANTHROPIC_API_KEY`
   - `BACKEND_USE_SUPABASE=true` (render.yaml에 이미 있음)
   - `CORS_ORIGINS` — 실제 프론트 도메인(쉼표로 여러 개 가능). render.yaml에
     기본값이 있지만 배포 주소가 바뀌면 여기서 갱신한다.
   - (신형 Supabase 프로젝트면 필요 없음) `SUPABASE_JWT_SECRET` — 구형
     HS256 토큰을 쓰는 프로젝트만.
3. 배포 후 `https://<서비스명>.onrender.com/health`가 `{"status":"ok", ...}`를
   주는지 확인한다.
4. **프론트에도 반영**: `web/`을 배포한 Vercel 프로젝트의 Environment
   Variables에서 `VITE_API_BASE_URL`을 이 Render 주소로 바꾸고 Redeploy한다
   (Vite 환경변수는 빌드 시점에 번들에 박히므로 값만 바꾸고 재배포 안 하면
   반영되지 않는다).

**무료 요금제 주의**: Render 무료 플랜은 15분 동안 요청이 없으면 컨테이너를
재운다. 다시 요청이 오면 깨우는 데 30초~1분 정도 걸린다(콜드 스타트) —
시연·심사 직전에 한 번 `/health`를 미리 호출해 깨워 두면 좋다.

## 엔드포인트

| | | 비용 |
|---|---|---|
| `GET /health` | 상태 확인 | 무료 |
| `GET /business-types` | 업종 목록 | 무료 |
| `GET /districts?business_code=` | 상권 목록 | 무료 |
| `POST /rank` | 조건 기반 전체 재랭킹 (PRD §16) | 무료 (결정론적, Claude 미사용) |
| `POST /districts/{code}/agents` | 추천/반대 근거 생성 + 검증 (PRD §10) | **Claude API 과금 발생** |
| `POST /report` | Top-K 상권 + 각각의 추천/반대 근거를 PDF 한 장으로 (PRD F-15) | **Claude API 과금 발생** (상권당 최대 2회, `top_k` 1~10) |

`/rank`와 `/districts/{code}/agents`를 분리해 둔 이유: 랭킹은 서울 전체
후보(1,000개 이상)를 매번 다시 계산해야 하므로 비용이 드는 Claude 호출을
여기 넣으면 안 되고, 근거 생성은 사용자가 실제로 펼쳐본 상위 몇 곳에 대해서만
필요하다. `/report`는 그 근거 생성을 Top-K개만큼 자동으로 반복해 PDF로
묶어주는 것뿐 — 새 판정 로직은 없다(`report.py`는 HTML 렌더링 + PDF 변환만).

### `/report` 사용 예

```bash
# 로그인 필수 — TOKEN 은 웹에서 로그인한 뒤 받은 Supabase access_token
curl -X POST http://localhost:8000/report \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"business_code":"CS100010","budget":5000,"top_k":3}' \
  -o report.pdf
```

### macOS에서 PDF가 안 만들어질 때 (WeasyPrint)

WeasyPrint는 Pango/cairo/glib를 시스템 라이브러리로 불러온다. Apple
Silicon Homebrew(`/opt/homebrew`)는 기본 라이브러리 탐색 경로에 없어서
`cannot load library 'libgobject-2.0-0'` 같은 에러가 날 수 있다.

```bash
brew install pango   # cairo/glib/harfbuzz 등 의존성도 같이 설치됨
```

설치만 하면 된다 — `report.py`가 macOS에서 `DYLD_LIBRARY_PATH`를 자동으로
맞춰준다(Linux 서버 배포 시에는 이 문제 자체가 거의 없다).

### Windows에서 PDF가 안 만들어질 때 (WeasyPrint)

`pip install weasyprint`만으로는 부족하다. Pango(GTK) 라이브러리가 따로 필요하고,
없으면 `cannot load library 'libgobject-2.0-0'` 오류가 난다.

1. https://www.msys2.org 에서 MSYS2를 설치한다.
2. MSYS2 터미널에서 `pacman -S mingw-w64-x86_64-pango`
3. 백엔드를 띄우는 터미널에서 DLL 경로를 알려준다.
   ```bat
   set WEASYPRINT_DLL_DIRECTORIES=C:\msys64\mingw64\bin
   uvicorn main:app --reload --port 8000
   ```

설치하지 않아도 서버는 뜬다. WeasyPrint는 `/report`를 호출할 때만 불러오고,
불러오지 못하면 그 요청만 503과 안내 문구를 돌려준다(Claude를 부르기 전에
확인하므로 과금되지 않는다).

## 모델 버전

`/rank`는 아직 LightGBM이 아니라 `scoring.py`의 휴리스틱 Score를 쓴다.
응답의 `model_version: "heuristic-v0"`로 항상 명시한다. LightGBM이 준비되면
`scoring.rank_districts()`의 `stability_score` 계산 부분만 모델 추론으로
바꾸면 되고, 응답 스키마(`RankResponse`)는 그대로 유지된다.

## 세션 기록

`/rank` 호출은 Supabase가 연결돼 있으면 `search_sessions` /
`recommendation_runs` / `recommendations`에 결과를 기록한다(PRD §22 Data
Flywheel). 기록에 실패해도 응답 자체는 막지 않는다 — 부가 기능이다.

---

### `GET /districts/{district_code}/detail?business_code=...`

상세 화면(PRD §17.4)이 쓰는 상권 진단 4영역 + 시계열. 구현은 `detail.py`.

**Claude 를 호출하지 않는다** — 전부 Pandas 집계라 과금이 없고, 그래서 상권을
열 때마다 바로 불러도 된다. 근거 문장 생성만 `/agents` 로 분리돼 있다.

```
diagnostics  잠재고객 / 경쟁강도 / 영업환경 / 비용
series       hourly(시간대별 유동인구) · sales · closure · competition
```

백분위는 `verification_tools.percentile()` 을 그대로 쓴다. 화면이 "상위 12%"
라고 말하는데 검증 Tool 이 다르게 판정하면 같은 숫자를 두고 서로 다른 말을
하게 되므로, 집계는 전부 백엔드에서 하고 프론트는 그리기만 한다.

없는 데이터는 지어내지 않는다.

| 상황 | 응답 |
|---|---|
| 분기가 1개뿐 | `series.sales.available=false` + `reason` 에 이유 |
| 시간대별 컬럼 미수집 | `series.hourly.available=false` |
| `store_count=0` 또는 배후수요 결측 | 경쟁강도 영역 `available=false`, `series.competition=null` |
| 임차료·공실률 | 비용 영역은 **항상** `available=false` (데이터셋에 없음) |

`extra_features` 가 JSONB 로 오는 Supabase 경로와 평면 컬럼으로 오는 로컬
디버그 CSV 경로를 둘 다 읽는다.
