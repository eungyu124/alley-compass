# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 상태

골목 컴퍼스(Alley Compass) — 서울 골목상권 생존 안정성 기반 AI 입지 추천 엔진.
프로젝트 개요·빠른 시작·설계 원칙은 루트 `README.md`에 있다. 이 파일은 그 위에
**코드를 고칠 때 필요한 맥락**만 담는다.

현재 있는 것은 PRD + DB 스키마 + ETL 파이프라인 + 검증 Tool 6종 + Agent 체인
(Recommendation / Risk / Verification, `alley_compass_etl/narrative_agents.py`) +
FastAPI 백엔드(`backend/`) + LightGBM 학습 파이프라인(`ml/`) + React 프론트
(목업 데이터)다.

- Agent 체인은 결정론적 부분(Fact Sheet)까지는 실데이터로 검증됐지만, Claude
  API 호출 자체는 계정 크레딧 부족으로 아직 라이브 테스트 전이다.
- `backend/`는 동작하지만 `/rank`는 아직 LightGBM이 아니라 `backend/scoring.py`의
  휴리스틱 Score(`model_version: "heuristic-v0"`)를 쓴다.
- `ml/train.py`는 Label 정의·Temporal Split·평가지표까지 구현·`--synthetic`으로
  배관 검증했지만, 실제 학습에 쓸 다분기 `district_features`가 아직 없다
  (현재 1개 분기만 수집됨). 모델이 준비되면 `backend/scoring.py`의
  `stability_score` 계산 부분만 교체하면 된다.

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

웹 프론트는 `web/`에 있다 (React 19 + Vite).

```bash
npm --prefix web install
npm --prefix web run dev      # http://localhost:5173
npm --prefix web run build
```

`docs/prototype-v0.html`은 React로 이식되기 전의 원본 프로토타입이다. 디자인 레퍼런스로
남겨둔 것이며 앱의 일부가 아니다 — 화면을 고칠 때는 `web/` 쪽만 수정한다.

테스트 프레임워크·린터 설정은 없다. 현재 회귀 확인 수단은 두 Python CLI의 데모 실행,
ETL이 출력하는 DATA QUALITY REPORT, `npm --prefix web run build`다. 화면 로직을 크게
건드렸다면 `react-dom/server`의 `renderToString`으로 Drawer를 업종×상권 전 조합 렌더해
NaN/undefined 노출을 훑는 방법이 빠르다 (임시 스크립트를 만들어 `vite build --ssr`로 돌린 뒤 지운다).

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
revoke + FastAPI가 service_role로 대행. `authenticated` 정책들은 MVP 기간 사실상
비활성이며 향후 로그인 도입용으로 남겨둔 것이다.

`web/` — React 프론트. 상세는 `web/README.md`. 요점만:

- 데이터는 **전부 목업**이다(`src/data/mockDistricts.js`의 상권 10곳 · 업종 5종). 점수·근거
  문장·검증 로그도 임시 계산이며 실제 분석 결과가 아니다. 화면의 "프로토타입 · 목업 데이터"
  배지와 푸터 고지는 실데이터 연결 전까지 지우지 않는다.
- **교체 지점은 `src/data/dataSource.js` 하나다.** 컴포넌트는 데이터 출처를 모른다.
- `src/lib/`의 계산 함수는 전역 state를 읽지 않고 `(상권, 조건)`만 받는 순수 함수다 —
  나중에 백엔드로 옮기기 쉽도록. 조건 state는 `App.jsx`가 단독으로 소유한다.
- 근거 문장은 HTML 문자열이 아니라 조각 배열(`["점포당 배후수요 ", b("2.9"), " ..."]`)로
  표현하고 `<Rich/>`로 렌더한다. `dangerouslySetInnerHTML`을 쓰지 않으며, pill처럼 평문이
  필요한 곳은 `plain(parts)`를 쓴다. Agent가 생성한 문장을 받을 때도 이 형태를 유지한다.
- `lib/scoring.js`는 LightGBM이 아니라 임시 휴리스틱이고, `lib/reasons.js`·
  `lib/verification.js`는 각각 Recommendation·Risk Agent와 verification_claims 조회가
  들어올 자리다. 각 파일 상단 주석에 그 대응이 적혀 있다.
- `lib/stats.js`의 백분위 정의는 `verification_tools.py`의 `percentile()`과 같아야 한다.
  화면의 "상위 N%"와 검증 Tool의 판정이 어긋나면 안 되기 때문이다.

## 이 코드베이스의 규칙

- **숫자는 코드가 증명한다** (PRD §10.3, §18). LLM은 집계·확률 계산을 직접 하지 않는다.
  `verification_tools.py`는 LLM을 호출하지 않으며 계속 결정론적으로 유지한다.
- **없는 데이터를 만들어내지 않는다.** 5종 데이터셋에 상권 면적이 없으므로
  `competition_density`(면적 기반 밀도) 컬럼은 NULL로 둔다. 경쟁강도는 면적이 필요 없는
  **점포당 배후수요** `= (유동+상주+직장) / store_count`로 계산한다 — 클수록 경쟁 여유이며,
  점포수 비율과 방향이 반대다. 이 정의는 세 곳이 공유하므로 한쪽만 바꾸면 안 된다:
  ETL의 `extra_features.demand_per_store`, `verification_tools.competition_density()`,
  `web/src/lib/scoring.js`. 보증금/임대료는 여전히 미보유라 `budget_validator`는
  `verified_by_data=False`를 반환한다.
  `districts.gu_name / latitude / longitude`도 같은 이유로 NULL이다 — 지도 기능은
  "영역-상권" 데이터나 별도 geocoding 단계가 선행되어야 한다.
- 데이터 부족을 추정으로 메우지 않는다. `trend()`는 분기가 모자라면
  `available=False`와 이유를 반환하고, QoQ 성장률은 직전 행이 실제 직전 분기일 때만 계산한다.
- 2026-07-03 서울시 제공 기준 변경 때문에 `MIN_SUPPORTED_QUARTER = 20211` 미만은 거부한다.
- `DISTRICT_LEVEL_METRICS` vs `BUSINESS_LEVEL_METRICS` 구분을 지킨다. flat 테이블에서
  상권 단위 지표는 업종 행마다 반복 저장되므로, 상권 간 비교 시 `drop_duplicates`로
  중복을 제거해야 percentile이 왜곡되지 않는다.
- `SUPABASE_SECRET_KEY`는 서버 전용이다. 프론트엔드 번들(React/Vite)에 넣지 않는다.
  프론트가 직접 읽어야 하는 것은 공개 읽기 정책이 걸린 테이블뿐이다.
- `data/raw/`와 `data/processed/`는 gitignore 대상이며 파이프라인이 재생성한다.
