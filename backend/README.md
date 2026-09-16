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

## 엔드포인트

| | | 비용 |
|---|---|---|
| `GET /health` | 상태 확인 | 무료 |
| `GET /business-types` | 업종 목록 | 무료 |
| `GET /districts?business_code=` | 상권 목록 | 무료 |
| `POST /rank` | 조건 기반 전체 재랭킹 (PRD §16) | 무료 (결정론적, Claude 미사용) |
| `POST /districts/{code}/agents` | 추천/반대 근거 생성 + 검증 (PRD §10) | **Claude API 과금 발생** |

`/rank`와 `/districts/{code}/agents`를 분리해 둔 이유: 랭킹은 서울 전체
후보(1,000개 이상)를 매번 다시 계산해야 하므로 비용이 드는 Claude 호출을
여기 넣으면 안 되고, 근거 생성은 사용자가 실제로 펼쳐본 상위 몇 곳에 대해서만
필요하다.

## 모델 버전

`/rank`는 아직 LightGBM이 아니라 `scoring.py`의 휴리스틱 Score를 쓴다.
응답의 `model_version: "heuristic-v0"`로 항상 명시한다. LightGBM이 준비되면
`scoring.rank_districts()`의 `stability_score` 계산 부분만 모델 추론으로
바꾸면 되고, 응답 스키마(`RankResponse`)는 그대로 유지된다.

## 세션 기록

`/rank` 호출은 Supabase가 연결돼 있으면 `search_sessions` /
`recommendation_runs` / `recommendations`에 결과를 기록한다(PRD §22 Data
Flywheel). 기록에 실패해도 응답 자체는 막지 않는다 — 부가 기능이다.
