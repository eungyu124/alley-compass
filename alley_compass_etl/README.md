# 골목 컴퍼스 ETL

## 1. 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env`에 다음 3개를 입력합니다.

- `SEOUL_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`

`SUPABASE_SECRET_KEY`는 서버 전용입니다. 프론트엔드에 넣지 않습니다.

---

## 2. 먼저 1분기 + 1업종으로 테스트

```bash
python alley_compass_etl.py \
  --start-quarter 20251 \
  --end-quarter 20251 \
  --business-name "커피-음료" \
  --no-upload
```

정상이라면 `data/processed/` 아래에 다음 파일이 생깁니다.

- `districts.csv`
- `business_types.csv`
- `district_features_debug.csv`

---

## 3. Supabase까지 실제 업로드

```bash
python alley_compass_etl.py \
  --start-quarter 20251 \
  --end-quarter 20251 \
  --business-name "커피-음료"
```

---

## 4. MVP 업종 여러 개

서울시 실제 업종명과 일치해야 합니다.

```bash
python alley_compass_etl.py \
  --start-quarter 20241 \
  --end-quarter 20252 \
  --business-name "커피-음료" \
  --business-name "세탁소" \
  --business-name "편의점" \
  --business-name "한식음식점" \
  --business-name "미용실"
```

업종명을 잘못 입력하면 스크립트가 사용 가능한 업종명 목록을 출력합니다.

실제로 Supabase에 올라가 있는 건 위 예시보다 외식업(CS1) 쪽이 더 두텁습니다 —
한식·중식·일식·양식·제과점·치킨·분식·호프-간이주점·커피-음료(CS1 10개 세부업종
중 9개, Supabase 무료 티어 저장 용량 여유를 남기려고 패스트푸드점만 제외)입니다.

---

## 5. 전체 업종

`--business-name`, `--business-code`를 모두 생략하면 전체 생활밀접업종을 처리합니다.

```bash
python alley_compass_etl.py \
  --start-quarter 20211 \
  --end-quarter 20252
```

주의: 데이터와 Supabase row 수가 크게 증가합니다. 대회 MVP에서는 필요한 업종부터 넣는 것을 권장합니다.

---

## 6. RAW를 다시 받고 싶을 때

```bash
python alley_compass_etl.py ... --refresh
```

기본값은 이미 받은 RAW CSV를 재사용합니다.

---

## 7. Verification Tools (PRD §11)

Recommendation/Risk Agent가 만든 문장을 원본 데이터와 대조하는 6개 Tool.
LLM을 쓰지 않고 전부 Pandas/통계 연산으로 판정한다 (PRD §10.3, §18).

```bash
# 로컬 CSV(district_features_debug.csv)로 데모 실행
python verification_tools.py

# 상권/업종 지정
python verification_tools.py --district-code 3120014 --business-code CS100010

# Supabase에 실제로 업로드된 데이터로 실행
python verification_tools.py --supabase
```

데모는 PRD §10.3의 "상위 8% → 검증 → 상위 약 10%로 정정" 흐름을 실제
데이터로 재현하고, 이어서 `verify_claim()` 배치 실행 예시를 보여준다.

| Tool | 함수 |
|---|---|
| Data Lookup Tool | `data_lookup()` |
| Percentile Tool | `percentile()` |
| Trend Calculator | `trend()` |
| Competition Density Tool | `competition_density()` |
| Budget Validator | `budget_validator()` |
| Assertion Validator | `assertion_validator()` |

`verify_claim(df, Claim(...))`이 위 6개를 `verification_type`에 따라
호출하는 통합 디스패처이고, `to_verification_claim_row()`는 결과를
`verification_claims` 테이블 insert용 행으로 변환한다 — 나중에
Recommendation/Risk/Verification 에이전트 체인을 붙일 때 그대로 쓰면 된다.

**Budget Validator에 대한 주의**: 5종 공식 데이터셋에는 보증금/임대료가
없다. 그래서 이 Tool은 "추정 보증금과 예산 비교의 산술이 맞는가"만
검증하고, 추정 보증금 자체가 데이터로 검증된 값이 아니라는 사실을
결과의 `note`에 항상 남긴다. 실측을 붙이려면 부동산원 상업용 부동산
임대조사를 상권_코드에 매핑하는 단계가 선행되어야 한다.

**Competition Density Tool의 판정 기준**: 면적이 없으므로 `점포당 배후수요`
(= (유동+상주+직장) / 점포수)로 비교한다. `ratio_to_avg`는 이 값의 서울 평균
대비 비율이며 **클수록 경쟁이 여유롭다** — 점포수 비율과 방향이 반대다.
배후수요 컬럼이 전부 비어 있으면 점포수 기준으로 물러서고, 어느 쪽으로
판정했는지는 결과의 `basis` 필드(`demand_per_store` / `store_count`)에 남는다.

---

## 8. Recommendation / Risk / Verification Agent 파이프라인 (PRD §9~§10)

```
Feature(district_features)
    → Fact Sheet          (fact_sheet.py, 결정론적 코드 — Claude 호출 없음)
    → Recommendation Agent / Risk Agent  (narrative_agents.py, Claude)
    → Verification Agent  (narrative_agents.verify_and_correct, Assertion Validator)
    → Verified Result
```

`.env`에 `ANTHROPIC_API_KEY`를 채운 뒤:

```bash
# Claude를 부르지 않고 Fact Sheet만 확인 (API 키 없어도 됨)
python pipeline.py --dry-run

# 전체 실행: 추천/반대 근거 생성 → 검증 → 정정
python pipeline.py

# 상권/업종/조건 지정
python pipeline.py --district-code 3120014 --business-code CS100010 \
  --budget 3000 --age 20 --character resident --priority survival
```

**왜 이렇게 나눴는가** — Claude는 새 숫자를 계산하지 않는다(PRD §1).
`fact_sheet.py`가 `verification_tools.py`의 6개 Tool로 "이 문장에 쓸 수
있는 사실"을 먼저 확정해 Claude에게 넘기고, Claude는 그 중 골라서
문장을 쓰고 어떤 사실(`fact_key`)을 근거로 어떤 수치(`stated_value`)를
적었는지 함께 반환한다. Verification Agent는 그 수치가 Fact의 실제
값과 허용오차 내에서 일치하는지만 `assertion_validator()`로 확인한다
— 불일치하면 정확한 값을 알려주고 딱 한 번 재작성을 요청하고
(PRD §10.3), 그래도 틀리면 그 문장은 최종 결과에서 제외한다
(PRD §18, Unsupported Claim Rate 목표 0%).

LightGBM 생존 안정성 Score는 `backend/scoring.py`의 `/rank`엔 이미 연결돼
있지만, 이 Fact Sheet/Agent 파이프라인엔 아직 포함되어 있지 않다 — 추천/반대
근거 문장이 인용하는 수치는 여전히 원본 feature 기반이다. 모델 예측을 여기도
쓰려면 `fact_sheet.build_fact_sheet()`에 한 줄 추가하면 된다.

---

## 현재 스키마와 관련된 의도적 NULL

### `competition_density`

5종 데이터에는 상권 면적이 없으므로 `점포수/면적` 같은 실제 밀도를 만들 수 없습니다.
따라서 이 컬럼(면적 기반 밀도를 뜻함)은 가짜 값을 넣지 않고 NULL로 둡니다.

대신 면적이 필요 없는 경쟁강도를 `extra_features`에 파생해 넣습니다.

```
backing_demand   = 유동인구 + 상주인구 + 직장인구
demand_per_store = backing_demand / store_count
```

값이 클수록 점포 하나가 나눠 갖는 수요가 커서 경쟁이 여유롭다는 뜻입니다.
`store_count`가 0이면 나누지 않고 NULL로 둡니다.

`verification_tools.competition_density()`와 `backend/scoring.py`의
`_competition_score()`가 같은 정의를 사용하므로, 화면에 보이는 경쟁강도와 검증
Tool의 판정 기준이 일치합니다. 프론트는 숫자를 직접 계산하지 않습니다 —
`backend/scoring.py` 한 곳에서만 계산합니다.

### `districts.gu_name`, `latitude`, `longitude`, `area_m2`

5종 API의 상권 행에는 자치구/위경도/면적이 포함되지 않습니다. `district_geo.py`가
별도 API(`영역-상권`, TbgisTrdarRelm — 중심점 좌표 + 면적만 제공, 다각형 아님)로
이 값을 채웁니다.

```bash
python district_geo.py --upload
```

이미 `alley_compass_etl.py`로 한 번이라도 등장한 `district_code`에만 반영합니다
— 등장한 적 없는 상권을 새로 만들지 않습니다. **새 업종을 ETL로 추가한 뒤에는
이 스크립트를 다시 돌려야** 그 업종에만 등장하는 상권도 지도에 나옵니다(안 돌리면
좌표가 NULL로 남고, 지도에서 그 상권만 조용히 빠집니다 — 지어낸 좌표를 넣지
않는다는 원칙과 같습니다).
