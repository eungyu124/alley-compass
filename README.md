# 골목 컴퍼스 (Alley Compass)

> 서울 골목상권 생존 안정성 기반 AI 입지 추천 엔진 — 팀 AIM

기존 상권분석 서비스는 **장소를 먼저 고르게** 한다("연남동 어때?"). 그런데 생애 첫
창업자는 애초에 어디에 열지 모르는 상태에서 출발한다.

골목 컴퍼스는 순서를 뒤집는다. **창업자의 조건**(업종·예산·타깃·상권 성격·우선순위)을
받아 서울 골목상권 전체를 훑고, 미래 폐업위험을 예측해 검토할 만한 후보 5곳까지
좁혀준다. 추천 근거뿐 아니라 **반대 근거**도 함께 제시하고, AI가 만든 문장 속 수치는
원본 데이터 검증 Tool로 대조한 것만 남긴다.

> **AI가 설명하고, 숫자는 코드가 증명한다.**

---

## 현재 상태

완성된 서비스가 아니다. 부품은 다 있고, 실제 다분기 데이터로 학습·검증하는
마지막 단계가 남았다.

| 구성 | 상태 | 위치 |
|---|---|---|
| 기획 (PRD v1.1) | ✅ | [`docs/PRD.md`](docs/PRD.md) |
| DB 스키마 | ✅ 작성 완료 (Supabase 수동 적용, v1.2 service_role 패치 포함) | [`db/schema_v1.1.sql`](db/schema_v1.1.sql) |
| 데이터 수집 파이프라인 (ETL) | ✅ 동작 | [`alley_compass_etl/`](alley_compass_etl/) |
| 검증 Tool 6종 | ✅ 동작 (LLM 미사용, 결정론적) | [`alley_compass_etl/verification_tools.py`](alley_compass_etl/verification_tools.py) |
| Claude 에이전트 3종 | ✅ 동작 (Sonnet 5, 라이브 검증 완료) | [`alley_compass_etl/narrative_agents.py`](alley_compass_etl/narrative_agents.py), [`fact_sheet.py`](alley_compass_etl/fact_sheet.py), [`pipeline.py`](alley_compass_etl/pipeline.py) |
| 웹 프론트 | ✅ `backend/`에 연결됨 — 상권·업종·랭킹은 실제 데이터, 추천/반대 근거는 버튼으로 실제 Claude 호출 | [`web/`](web/) |
| FastAPI 백엔드 | ✅ 동작 (`/rank`, `/districts/{code}/agents`, `/report`), **랭킹 점수는 아직 휴리스틱** | [`backend/`](backend/) |
| PDF 리포트 (F-15) | ✅ 동작 (WeasyPrint) — Top-K 상권 + 실제 Claude 근거를 PDF 한 장으로 | `POST /report` |
| LightGBM 예측 모델 | ⚠️ 학습 파이프라인 완성, **실제 다분기 데이터로 학습 전** (합성 데이터로 배관만 검증) | [`ml/`](ml/) |

**웹 화면의 상권·업종·랭킹은 이제 실제 데이터다** (`alley_compass_etl.py`로 수집한 만큼만).
생존 안정성 Score는 LightGBM이 아니라 임시 휴리스틱이고, 지도·상세 시계열 차트는 아직
없다 — 자세한 건 [`web/README.md`](web/README.md)의 "지금 화면에서 진짜인 것/아직 아닌 것" 표 참고.

- **Claude 에이전트 3종**: `claude-sonnet-5`로 라이브 검증 완료 —
  `python alley_compass_etl/pipeline.py` 실행 결과 추천 근거 3개·반대 근거
  3개 전부 1차 생성에서 검증 통과(정정 0건). Opus 대신 Sonnet을 쓴 이유:
  Fact를 문장으로 옮기는 작업이라 어려운 추론이 필요 없고, Verification
  Agent가 어차피 수치를 재검증하는 안전망이 있어 비용(1/2.5)을 아꼈다.
- **LightGBM**: `alley_compass_etl.py`로 아직 1개 분기(20251)만 받아둔 상태라
  PRD §15의 Temporal Split(과거 학습 → 미래 검증)을 할 수 있는 다분기 데이터가
  없다. `ml/train.py --synthetic`으로 배관(라벨링·분할·학습·평가지표)이 실제로
  작동하는지는 확인했지만, 이건 합성 데이터라 실제 예측 성능이 아니다. 여러
  분기를 실제로 수집한 뒤 `ml/train.py`로 다시 학습해야 진짜 모델이 나온다.
- **FastAPI**: `/rank`는 지금 LightGBM 대신 원본 feature로 계산한 휴리스틱
  Score(`model_version: "heuristic-v0"`)를 쓴다. 모델이 준비되면
  `backend/scoring.py` 한 곳만 바꾸면 된다.

---

## 폴더 구조

```
alley-compass/
├── README.md               이 파일
├── CLAUDE.md               Claude Code용 작업 가이드
├── docs/
│   ├── PRD.md              제품 요구사항 정의서 v1.1 — 사양의 기준 문서
│   └── prototype-v0.html   React 이식 전 원본 프로토타입 (디자인 레퍼런스)
├── db/
│   └── schema_v1.1.sql     Supabase/PostgreSQL 스키마 (테이블 10개 + RLS)
├── alley_compass_etl/      서울시 Open API → 전처리 → Supabase 적재 → Agent
│   ├── alley_compass_etl.py    ETL 파이프라인
│   ├── verification_tools.py   검증 Tool 6종 (PRD §11)
│   ├── fact_sheet.py            Feature → Agent에게 건넬 사실(Fact) 목록 생성
│   ├── narrative_agents.py      Recommendation/Risk/Verification Agent (Claude)
│   ├── pipeline.py               위 전체를 잇는 CLI (--dry-run 지원)
│   └── README.md               ETL·Agent 사용법 · 의도적 NULL 설명
├── backend/                FastAPI — 위 모듈들을 엔드포인트로 노출
│   ├── main.py                  /rank, /districts/{code}/agents 등
│   ├── scoring.py                랭킹 로직 (현재 휴리스틱, LightGBM 대기)
│   └── README.md
├── ml/                     LightGBM 생존 안정성 모델 (PRD §14~§15)
│   ├── labels.py                 Label 정의 (PRD §7.1)
│   ├── features.py               Feature 목록
│   ├── train.py                  Temporal Split 학습·평가 (--synthetic 배관 점검)
│   └── README.md
└── web/                    React 19 + Vite 프론트엔드
    ├── src/
    └── README.md           구조 · 실데이터 연결 절차
```

---

## 빠른 시작

### 웹 화면 보기 (준비물 없음)

```bash
cd web
npm install
npm run dev        # http://localhost:5173
```

목업 데이터로 즉시 돌아간다. 조건을 바꾸면 랭킹이 재계산되고, 상권 행을 누르면
진단·근거·검증 로그가 담긴 상세 패널이 열린다.

### 데이터 파이프라인 돌리기 (API 키 필요)

준비물: ① [서울 열린데이터광장](https://data.seoul.go.kr) 인증키 ② Supabase 프로젝트에
`db/schema_v1.1.sql` 적용

```bash
cd alley_compass_etl
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env                              # 키 3개 입력

# 먼저 1분기 × 1업종으로, 업로드 없이 시험
python alley_compass_etl.py --start-quarter 20251 --end-quarter 20251 \
  --business-name "커피-음료" --no-upload

python verification_tools.py                      # 검증 Tool 데모
```

자세한 옵션은 [`alley_compass_etl/README.md`](alley_compass_etl/README.md).

---

## 설계 원칙

이 프로젝트가 다른 상권분석 서비스와 다른 지점이자, 코드를 고칠 때 지켜야 할 선이다.

**1. 숫자는 코드가 증명한다.** LLM은 집계나 확률 계산을 직접 하지 않는다. Pandas와 ML
모델이 계산한 값만 쓴다. `verification_tools.py`는 LLM을 호출하지 않는 순수 계산 코드이며
앞으로도 그래야 한다.

**2. 없는 데이터를 지어내지 않는다.** 5종 공개 데이터셋에 상권 면적이 없으므로 면적
기반 밀도(`competition_density`)는 NULL로 둔다. 대신 면적이 필요 없는 **점포당 배후수요**
`= (유동+상주+직장) / 점포수`로 경쟁강도를 잰다. 보증금·임대료도 미보유라 예산 검증은
"산술이 맞는가"까지만 하고 `verified_by_data=False`를 남긴다. 화면에서도 비용 영역은
점수를 만들지 않고 "데이터 미보유"로 표시한다.

**3. 데이터 부족을 추정으로 메우지 않는다.** 분기가 모자라면 추세를 만들어내지 않고
`available=False`와 이유를 반환한다. QoQ 성장률은 직전 행이 실제 직전 분기일 때만 센다.

**4. 절대 점수보다 분포 내 위치.** "몇 점"보다 "서울 골목상권 중 상위 몇 %"가 의사결정에
쓸모 있다. 백분위 정의는 Python 검증 Tool과 웹이 동일하다.

---

## 데이터 출처

서울시 「우리마을가게 상권분석서비스」 (제공: 서울신용보증재단 · 서울 열린데이터광장),
공공누리 제1유형. 6개 API를 사용한다 — 길단위인구 · 점포 · 추정매출 · 집객시설 ·
직장인구 · 상주인구. (PRD는 뒤 둘을 "배후 인구" 하나로 묶지만 실제 API는 2개다.)

2026-07-03 서울시 제공 기준 변경을 반영해 **2021년 이후 데이터만** 사용한다.

Score는 개별 점포의 생존확률이 아니라 **상권 × 업종 단위**의 폐업위험/생존 안정성
지표다. 개인 점포 생존확률로 해석하지 않는다.
