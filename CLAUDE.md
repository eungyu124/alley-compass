# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 상태

골목 컴퍼스(Alley Compass) — 서울 골목상권 생존 안정성 기반 AI 입지 추천 엔진.
프로젝트 개요·빠른 시작·설계 원칙은 루트 `README.md`에 있다. 이 파일은 그 위에
**코드를 고칠 때 필요한 맥락**만 담는다.

현재 있는 것은 PRD + DB 스키마 + ETL 파이프라인 + 상권 좌표 보강
(`alley_compass_etl/district_geo.py`) + 검증 Tool 6종 + Agent 체인
(Recommendation / Risk / Verification, `alley_compass_etl/narrative_agents.py`) +
자연어 조건 파싱(`alley_compass_etl/condition_parser.py`) + FastAPI 백엔드
(`backend/`) + LightGBM 학습·배포 파이프라인(`ml/`) + React 프론트(`web/`,
백엔드 실데이터 연결 · Supabase Auth 로그인 필수 · 카카오맵)다. 백엔드는 Render에,
프론트는 Vercel에 배포돼 있다.

- Agent 체인은 `claude-sonnet-5`로 라이브 검증까지 됐다. 다만 표본이 상권 1곳이라
  재작성률·폐기율 측정이 남았고, 결과는 아직 화면 표시용일 뿐
  `agent_analyses`/`verification_claims` 테이블엔 안 쌓인다.
- `/rank`는 `backend/scoring.py`의 `rank_districts()`가 계산한다. `backend/models/`에
  LightGBM 아티팩트(`*.joblib`)가 승격돼 있으면 그 예측을 `stability_score`로 쓰고
  (`model_version: "lightgbm-<버전>"`), 없거나 예측이 실패하면 원본 feature 기반
  휴리스틱(`"heuristic-v0"`)으로 조용히 대체한다. 최종 `final_score`는
  `stability_score`와, 사용자가 고른 상권성격(character)·연령대(age) 기반
  `target_fit_score`의 가중합이다(PRD §16) — `character`/`age`를 바꿔도 정렬
  순서가 실제로 바뀌어야 한다는 뜻이므로, 이 두 값을 다시 분리하는 리팩터링을
  할 땐 반드시 실 데이터로 순위 변화를 재확인한다.
- LightGBM은 실제로 학습·배포됐다(2021Q1~2025Q2, 18개 분기, 10개 업종, 22.5만 행).
  `ml/train.py`의 `--train-start-quarter`로 급성 코로나 구간(2021~2022)을 학습에서
  제외할 수 있다 — 그 구간 라벨이 점포 수가 적은 상권에서 쉽게 오염되는 걸
  실측으로 확인했기 때문이다. `backend/scoring.py`는 여기에 더해 점포수가 적은
  상권을 업종별 중앙값에 비례한 베이지안 축소로 한 번 더 보정한다
  (`STORE_COUNT_CONFIDENCE_K_FRAC`, `MIN_STORE_COUNT_ANY_TRUST`) — 고정 상수로
  바꾸면 업종마다 점포수 규모가 달라 다시 안 맞을 수 있으니 주의한다.
  모델이 바뀌면 `ml/models/`(실험용, gitignore)가 아니라 **`backend/models/`**에
  같은 버전 문자열로 복사해야 배포판에 실린다.

새 컴포넌트를 만들 때는 `docs/PRD.md`가 사양의 기준 문서다 (§9~§11 Agent/Tool,
§14~§15 모델·Temporal Split, §19 기술 스택).

문서·주석·로그·에러 메시지는 모두 한국어다. 새 코드도 같은 언어를 유지한다.

## 명령어

모든 Python 명령은 `alley_compass_etl/`에서 실행한다 (`--data-dir` 기본값이 cwd 기준
`data/`이고, `verification_tools`가 `alley_compass_etl` 모듈을 import한다).

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env    # SEOUL_API_KEY / SUPABASE_URL / SUPABASE_SECRET_KEY

# 스모크 테스트: 1분기 × 1업종, 업로드 없이 로컬 CSV까지만
python alley_compass_etl.py --start-quarter 20251 --end-quarter 20251 \
  --business-name "커피-음료" --no-upload

# Supabase 업로드 포함
python alley_compass_etl.py --start-quarter 20241 --end-quarter 20252 \
  --business-name "커피-음료" --business-name "세탁소"

python alley_compass_etl.py ... --refresh     # RAW CSV 캐시 무시하고 재다운로드

# Verification Tool 데모 (PRD §10.3 흐름 재현 + verify_claim 배치 예시)
python verification_tools.py
python verification_tools.py --district-code 3120014 --business-code CS100010
python verification_tools.py --supabase       # 로컬 CSV 대신 Supabase에서 로드
```

웹 프론트는 `web/`에 있다 (React 19 + TypeScript + Vite + Tailwind 4 + Radix).
backend가 :8000에 떠 있어야 화면이 동작한다.

```bash
npm --prefix web install
npm --prefix web run dev        # http://localhost:5173
npm --prefix web run build      # tsc -b && vite build
npm --prefix web run typecheck  # 타입만 검사
```

`docs/prototype-v0.html`은 React로 이식되기 전의 원본 프로토타입이다. 디자인 레퍼런스로
남겨둔 것이며 앱의 일부가 아니다 — 화면을 고칠 때는 `web/` 쪽만 수정한다.

테스트 프레임워크·린터 설정은 없다. 현재 회귀 확인 수단은 두 Python CLI의 데모 실행,
ETL이 출력하는 DATA QUALITY REPORT, `npm --prefix web run build`(타입 검사 포함)다.
백엔드 집계를 고쳤다면 합성 프레임을 만들어 `build_detail()`을 직접 호출해보는 편이 빠르다 —
분기 1개·컬럼 결측·`store_count=0` 같은 경계를 실데이터 없이 훑을 수 있다.

## 아키텍처

```
서울 열린데이터광장 Open API (6개 서비스)
   ↓ download_raw_data()   data/raw/<key>/<quarter>.csv  ← 파일 존재 시 캐시 재사용
   ↓ prep_*() + build_feature_table()
상권×업종×분기 flat feature table
   ↓ data/processed/district_features_debug.csv  (Verification Tool의 기본 입력)
   ↓ upload_features()
Supabase: districts / business_types / district_features
```

`alley_compass_etl.py`

- `SERVICES` 딕셔너리가 6개 서울시 API를 정의한다. `quarter_filter: True`인 것
  (foot/stores/sales)은 분기별 URL로 받고, 나머지(facilities/worker/resident)는 전량
  받아 `load_unfiltered()`에서 분기 범위로 자른다. PRD가 "배후 인구" 하나로 묶은 항목이
  실제 API에서는 worker/resident 2개 서비스다.
- 조인 기준은 `stores LEFT JOIN sales` (점포는 있는데 카드매출이 없는 행을 보존), 그 위에
  상권 단위 데이터(foot/worker/resident/facilities)를 `many_to_one`으로 붙인다. merge마다
  `validate=`를 지정해 키 중복이 조용히 행을 늘리지 못하게 한다.
- `district_features`의 고정 컬럼 외 값은 전부 `extra_features` JSONB로 간다
  (`build_feature_records()`의 `extra_cols`). 새 원천 컬럼을 추가할 때 스키마를
  바꾸지 말고 이 리스트에 넣는 것이 기본이다.
- `source_dates` JSONB는 PRD §13 요구사항(Feature별 기준시점 추적)의 구현이다. 값이
  실제로 존재하는 데이터셋만 분기를 기록한다.
- Supabase 적재는 전부 upsert다. 충돌 키: `districts.district_code`,
  `business_types.business_code`, `district_features(district_id, business_type_id,
  reference_date)`. 재실행이 멱등이어야 하므로 새 테이블을 붙일 때도 unique 제약을 먼저 정의한다.

`verification_tools.py` — PRD §11의 6개 Tool과 1:1 대응(`data_lookup`, `percentile`,
`trend`, `competition_density`, `budget_validator`, `assertion_validator`).
`verify_claim(df, Claim(...))`이 `verification_type`으로 디스패치하고,
`to_verification_claim_row()`가 결과를 `verification_claims` insert 행으로 바꾼다.
Agent 체인을 붙일 때 이 두 함수가 접합점이다.

`db/schema_v1.1.sql` — Supabase에 수동 적용하는 단일 스키마 파일(마이그레이션
도구 없음). 접근 전략: `districts/business_types/district_features/model_versions/
predictions`는 anon 읽기 공개, 개인 세션 계열 5개 테이블(`search_sessions` 이하)은 anon
revoke + FastAPI가 service_role로 대행. 로그인 사용자(`authenticated`)는 본인 기록
select만 가능하다 — 쓰기 정책은 v1.3 패치에서 제거했다(FastAPI 우회 방지).
v1.3은 `profiles`(가입 트리거 `handle_new_user`)도 추가한다. 패치는 파일 끝에
섹션으로 붙이고, 그 섹션만 따로 실행해도 되게 멱등하게 쓴다.

`backend/auth.py` — Supabase JWT 검증(`require_user`). 신형 프로젝트는 JWKS(ES256/RS256),
구형은 `SUPABASE_JWT_SECRET`(HS256). `/health` 외 모든 엔드포인트에 붙어 있고,
`/rank`는 검증된 `user_id`로 `search_sessions`를 기록한다.

`backend/detail.py` — 상세 화면용 집계(진단 4영역 + 시계열). `/rank`·`/agents`와 달리
Claude를 부르지 않으므로 과금이 없다. 백분위는 `verification_tools.percentile()`을 그대로
쓴다 — 화면의 "상위 N%"와 검증 Tool의 판정이 어긋나면 안 되기 때문이다. 평면 컬럼(로컬
디버그 CSV)과 `extra_features` JSONB(Supabase) 양쪽에서 값을 읽는다.

`backend/ratelimit.py` — `/parse-condition`·`/agents`·`/report`(전부 Claude 호출)에
사용자별 1시간 크레딧 한도를 건다. 로그인만으로는 반복 호출을 못 막아서인데,
메모리 기반이라 인스턴스 하나에서만 유효하다(수평 확장 시 Redis 등으로 교체 필요).

`backend/main.py`의 `get_frame()`은 `district_features`를 프로세스 메모리에 캐시하고
`FRAME_CACHE_TTL_SECONDS`(기본 6시간)마다 자동으로 다시 읽는다. ETL로 새 분기를
Supabase에 올려도 이 시간 전엔 화면에 안 보인다 — 즉시 반영하려면 서버를 재시작한다.

`web/` — React + TypeScript 프론트. 상세는 `web/README.md`. 요점만:

- **프론트는 숫자를 계산하지 않는다.** 점수·백분위·진단은 전부 backend가 내려준 값이다.
  랭킹 로직이 `backend/scoring.py` 한 곳에만 있어야 화면과 검증 Tool이 어긋나지 않는다.
- **백엔드 호출은 `src/lib/api.ts` 하나를 지난다.** 컴포넌트가 `fetch`를 직접 쓰지 않는다.
  `src/types/api.ts`가 `backend/schemas.py`와 1:1로 대응하므로 스키마를 바꾸면 둘 다 고친다.
- `POST /districts/{code}/agents`는 Claude를 최소 4번 부른다(15~20초, 과금). 자동 호출하지
  않고 사용자가 버튼을 눌러야 부른다. `verified=false` 문장은 화면에 내보내지 않는다.
- 디자인 시스템은 `src/components/ui/`다. 화면 코드는 배럴(`@/components/ui`)에서만 가져온다.
  드롭다운·드로어·슬라이더는 Radix Primitives 위에 토큰만 입혔다 — 접근성 배선을 다시
  만들지 않는다.
- 디자인 토큰은 3계층이다: `tokens.primitive.css`(재료) → `tokens.semantic.css`(역할, 라이트·
  다크 둘 다) → `theme.css`(`@theme inline`으로 Tailwind 등록). 컴포넌트는 역할 토큰만 쓴다.
  **어떤 토큰도 다크 블록에만 존재해선 안 된다** — 라이트에서 색이 비어 렌더된다.
- 근거 문장은 HTML 문자열이 아니라 조각 배열(`["점포당 배후수요 ", b("2.9"), " ..."]`)로
  표현하고 `<Rich/>`로 렌더한다. `dangerouslySetInnerHTML`을 쓰지 않으며, 평문이 필요한
  곳은 `plain(parts)`를 쓴다.
- 조건 state는 `App.tsx`가 단독으로 소유한다. 주요 탭 4개(추천·물어보기·리포트·기록)도 라우터 없이
  `App.tsx`의 `tab` state로 전환한다(`lib/tabs.ts`). 기록 탭은 서버 목록 API가 없어 브라우저
  localStorage(`lib/historyStorage.ts`)만 쓴다 — 저장 범위를 바꾸면 개인정보처리방침도 고친다.
- 로그인은 `Root.tsx`가 관문이다. `/privacy`만 로그인 없이 열린다(라우터 없음, 경로 분기).
  Supabase 클라이언트(`lib/supabase.ts`)는 **로그인 전용**이고 데이터 조회에 쓰지 않는다.
  API 401은 `signOut(사유)`로 로그인 화면에 돌려보낸다.
- 개인정보처리방침(`components/legal/PrivacyPage.tsx`)은 코드 동작을 서술한다. 수집 항목·
  파기(`on delete set null`)·Claude 전송 범위를 바꾸면 이 페이지도 고친다.

## 이 코드베이스의 규칙

- **숫자는 코드가 증명한다** (PRD §10.3, §18). LLM은 집계·확률 계산을 직접 하지 않는다.
  `verification_tools.py`는 LLM을 호출하지 않으며 계속 결정론적으로 유지한다.
- **없는 데이터를 만들어내지 않는다.** 5종 데이터셋에 상권 면적이 없으므로
  `competition_density`(면적 기반 밀도) 컬럼은 NULL로 둔다. 경쟁강도는 면적이 필요 없는
  **점포당 배후수요** `= (유동+상주+직장) / store_count`로 계산한다 — 클수록 경쟁 여유이며,
  점포수 비율과 방향이 반대다. 이 정의는 세 곳이 공유하므로 한쪽만 바꾸면 안 된다:
  ETL의 `extra_features.demand_per_store`, `verification_tools.competition_density()`,
  `backend/scoring.py`의 `_competition_score()`, `backend/detail.py`의 `_competition_frame()`.
  보증금/임대료는 여전히 미보유라 `budget_validator`는 `verified_by_data=False`를 반환하고,
  화면의 비용 진단 영역도 항상 `available=false`다. 한국부동산원 R-ONE에 실데이터가
  있지만 전국 368개 "대표 상권" 단위라 서울시 1,638개 골목상권보다 훨씬 거칠어
  편입하지 않기로 했다(정밀도를 지어내지 않는다는 원칙).
  `districts.gu_name / latitude / longitude / area_m2`는 `alley_compass_etl/district_geo.py`
  (서울시 "영역-상권" API, 중심점+면적 — 다각형 아님)로 채운다. 이미
  `alley_compass_etl.py`로 한 번이라도 등장한 `district_code`에만 반영하고, 아직
  이 스크립트를 안 돌렸거나 나중에 새 업종 ETL로 새로 생긴 상권은 계속 NULL/None일
  수 있다 — `backend/main.py`의 `_none_if_nan()`이 pandas NaN을 Pydantic이 받는
  `None`으로 바꿔주므로, 새 업종을 추가했으면 `district_geo.py --upload`를 다시
  돌려 지도 데이터를 보강하는 걸 잊지 않는다.
- 데이터 부족을 추정으로 메우지 않는다. `trend()`는 분기가 모자라면
  `available=False`와 이유를 반환하고, QoQ 성장률은 직전 행이 실제 직전 분기일 때만 계산한다.
- 2026-07-03 서울시 제공 기준 변경 때문에 `MIN_SUPPORTED_QUARTER = 20211` 미만은 거부한다.
- `DISTRICT_LEVEL_METRICS` vs `BUSINESS_LEVEL_METRICS` 구분을 지킨다. flat 테이블에서
  상권 단위 지표는 업종 행마다 반복 저장되므로, 상권 간 비교 시 `drop_duplicates`로
  중복을 제거해야 percentile이 왜곡되지 않는다.
- `SUPABASE_SECRET_KEY`는 서버 전용이다. 프론트엔드 번들(React/Vite)에 넣지 않는다.
  프론트가 직접 읽어야 하는 것은 공개 읽기 정책이 걸린 테이블뿐이다.
- `data/raw/`와 `data/processed/`는 gitignore 대상이며 파이프라인이 재생성한다.
