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

완성된 서비스가 아니다. 예측 모델과 백엔드가 아직 없다.

| 구성 | 상태 | 위치 |
|---|---|---|
| 기획 (PRD v1.1) | ✅ | [`docs/PRD.md`](docs/PRD.md) |
| DB 스키마 | ✅ 작성 완료 (Supabase 수동 적용) | [`db/schema_v1.1.sql`](db/schema_v1.1.sql) |
| 데이터 수집 파이프라인 (ETL) | ✅ 동작 | [`alley_compass_etl/`](alley_compass_etl/) |
| 검증 Tool 6종 | ✅ 동작 (LLM 미사용, 결정론적) | [`alley_compass_etl/verification_tools.py`](alley_compass_etl/verification_tools.py) |
| Claude 에이전트 3종 | ⚠️ 구현 완료, **실제 API 응답 미검증** (계정 크레딧 필요) | [`alley_compass_etl/narrative_agents.py`](alley_compass_etl/narrative_agents.py), [`fact_sheet.py`](alley_compass_etl/fact_sheet.py), [`pipeline.py`](alley_compass_etl/pipeline.py) |
| 웹 프론트 | ⚠️ 화면 완성, **데이터는 목업** | [`web/`](web/) |
| LightGBM 예측 모델 | ❌ 미착수 | — |
| FastAPI 백엔드 | ❌ 미착수 | — |

**화면에 보이는 숫자는 아직 전부 목업이다.** 실데이터를 흘리려면 서울 열린데이터광장
API 키와 Supabase 프로젝트가 필요하다 (아래 빠른 시작 참고).

Claude 에이전트 3종(Recommendation/Risk/Verification)은 코드·구조·검증 로직까지 다
구현되어 있고 결정론적 부분(Fact Sheet 생성)은 실제 데이터로 확인됐지만, Claude API
호출 자체는 계정에 크레딧이 없어 아직 라이브로 못 돌려봤다 — 크레딧 채운 뒤
`python alley_compass_etl/pipeline.py` 로 확인.

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
