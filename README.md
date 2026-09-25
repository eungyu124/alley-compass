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

핵심 기능은 실 데이터·실 배포로 동작한다. 남은 건 근거 문장의 DB 적재(F/U 순환),
SHAP 설명력, 임차료 데이터 편입 여부 같은 다듬는 작업이다.

| 구성 | 상태 | 위치 |
|---|---|---|
| 기획 (PRD v1.1) | ✅ | [`docs/PRD.md`](docs/PRD.md) |
| DB 스키마 | ✅ 작성 완료 (Supabase 수동 적용, v1.2~v1.4 패치 포함 — service_role · 로그인 · 상권 면적) | [`db/schema_v1.1.sql`](db/schema_v1.1.sql) |
| 데이터 수집 파이프라인 (ETL) | ✅ 동작 — 외식업 10종 중 9종(패스트푸드점 제외) + 그 외 생활업종 다수, 2021Q1~2025Q2(18개 분기) | [`alley_compass_etl/`](alley_compass_etl/) |
| 상권 좌표/구/면적 보강 | ✅ 동작 (서울시 "영역-상권" API, 중심점+면적 — 다각형 아님) | [`alley_compass_etl/district_geo.py`](alley_compass_etl/district_geo.py) |
| 검증 Tool 6종 | ✅ 동작 (LLM 미사용, 결정론적) | [`alley_compass_etl/verification_tools.py`](alley_compass_etl/verification_tools.py) |
| Claude 에이전트 3종 | ✅ 동작 (Sonnet 5, 라이브 검증 완료) — 근거는 화면에만 표시, DB 적재는 아직 안 함 | [`alley_compass_etl/narrative_agents.py`](alley_compass_etl/narrative_agents.py), [`fact_sheet.py`](alley_compass_etl/fact_sheet.py), [`pipeline.py`](alley_compass_etl/pipeline.py) |
| 자연어 조건 입력 | ✅ 동작 — Claude 구조화 출력으로 문장을 조건으로 파싱, 이전 대화 조건 이어받음 | `POST /parse-condition` |
| 지도 | ✅ 동작 (카카오맵) — 상권 중심 좌표 + 면적 비례 원, 순위 배지, 더보기 페이지네이션 | [`web/src/components/RankMap.tsx`](web/src/components/RankMap.tsx) |
| 웹 프론트 | ✅ `backend/`에 연결됨 · 로그인 필수(Supabase Auth: 이메일 · Google · 카카오) · React 19 + TypeScript + Tailwind 4, 자체 디자인 시스템 | [`web/`](web/) |
| FastAPI 백엔드 | ✅ 동작 (`/rank`, `/parse-condition`, `/districts/{code}/detail`, `/agents`, `/report`), `/health` 외 전부 로그인 필요, **랭킹 점수는 LightGBM**(모델 없으면 휴리스틱 폴백) | [`backend/`](backend/) |
| 백엔드 배포 (Render, Docker) | ✅ 동작 — 무료 플랜, 최신 분기만 메모리에 올려 OOM 회피 | [`Dockerfile`](Dockerfile), [`backend/README.md`](backend/README.md#배포-render) |
| PDF 리포트 (F-15) | ✅ 동작 (WeasyPrint) — Top-K 상권 + 실제 Claude 근거를 PDF 한 장으로. 웹 화면 연결 전 | `POST /report` |
| LightGBM 예측 모델 | ✅ 실 데이터로 학습·배포 완료 — 22.5만 행(10개 업종 × 18개 분기) | [`ml/`](ml/), [`backend/models/`](backend/models/) |
| 보증금/임대료 | ❌ 미보유 — 한국부동산원 R-ONE에 실데이터가 있으나 전국 368개 "대표 상권" 단위라 서울시 1,638개 골목상권과 정밀도가 안 맞아 편입 보류(§설계 원칙 2) | — |

**웹 화면의 숫자는 이제 전부 실제 데이터다** (`alley_compass_etl.py`로 수집한 만큼만).
순위·상권 진단 4영역·시간대별 유동인구·분기별 매출/폐업률·지도까지 백엔드가 계산해
내려준다. 생존 안정성 Score는 LightGBM 예측이 기본이고(모델 파일이 없으면 휴리스틱으로
조용히 대체), 임차료만 여전히 데이터셋에 없어 빈칸으로 둔다 — 자세한 건
[`web/README.md`](web/README.md)의 "지금 진짜인 것 / 아직 아닌 것" 표 참고.

- **Claude 에이전트 3종**: `claude-sonnet-5`로 라이브 검증 완료 —
  `python alley_compass_etl/pipeline.py` 실행 결과 추천 근거 3개·반대 근거
  3개 전부 1차 생성에서 검증 통과(정정 0건). Opus 대신 Sonnet을 쓴 이유:
  Fact를 문장으로 옮기는 작업이라 어려운 추론이 필요 없고, Verification
  Agent가 어차피 수치를 재검증하는 안전망이 있어 비용(1/2.5)을 아꼈다.
  결과는 아직 화면에만 표시되고 `agent_analyses`/`verification_claims`
  테이블엔 안 쌓인다(다음 작업 후보).
- **LightGBM**: 2021Q1~2025Q2(18개 분기), 10개 업종, 22.5만 행 실데이터로 학습했다.
  학습 중 두 가지 실제 편향을 발견해 고쳤다 — ① 코로나 시기(2021~2022) 데이터가
  "폐업률 조금만 늘어도 불안정"으로 라벨을 오염시켜 `--train-start-quarter`로
  급성 코로나 구간을 학습에서 제외했고, ② 점포 1~2개짜리 상권은 폐업할 기회 자체가
  없어 "매우 안정적"으로 오인되는 소표본 편향을 발견해, `backend/scoring.py`에서
  업종별 점포수 중앙값에 비례한 베이지안 축소로 후처리한다. 모델 파일은
  `backend/models/*.joblib`에 "승격"해 커밋해야 배포판에 실린다.
- **FastAPI**: `/rank`는 LightGBM이 승격돼 있으면 그 예측을 쓰고
  (`model_version: "lightgbm-<버전>"`), 없으면 원본 feature로 계산한 휴리스틱
  Score(`"heuristic-v0"`)로 조용히 대체한다. 순위(`final_score`)는 생존 안정성
  Score와 사용자가 고른 상권성격·연령대 기반 타겟 적합도(`target_fit_score`)의
  가중합이다(PRD §16) — 우선순위(survival/growth)에 따라 가중치가 조정된다.

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
│   └── schema_v1.1.sql     Supabase/PostgreSQL 스키마 (테이블 11개 + RLS)
│                             끝에 v1.2(service_role) · v1.3(로그인) 패치 섹션
├── alley_compass_etl/      서울시 Open API → 전처리 → Supabase 적재 → Agent
│   ├── alley_compass_etl.py    ETL 파이프라인
│   ├── district_geo.py          상권 좌표/구/면적 보강 (지도 표시용, "영역-상권" API)
│   ├── condition_parser.py      자연어 문장 → 조건(ParsedCondition) 구조화 출력
│   ├── verification_tools.py   검증 Tool 6종 (PRD §11)
│   ├── fact_sheet.py            Feature → Agent에게 건넬 사실(Fact) 목록 생성
│   ├── narrative_agents.py      Recommendation/Risk/Verification Agent (Claude)
│   ├── pipeline.py               위 전체를 잇는 CLI (--dry-run 지원)
│   └── README.md               ETL·Agent 사용법 · 의도적 NULL 설명
├── backend/                FastAPI — 위 모듈들을 엔드포인트로 노출
│   ├── main.py                  /rank, /parse-condition, /districts/{code}/detail, /agents
│   ├── auth.py                   Supabase 로그인 토큰(JWT) 검증
│   ├── scoring.py                랭킹 로직 — LightGBM 승격돼 있으면 우선 사용, 없으면 휴리스틱 폴백
│   ├── detail.py                 상권 진단 4영역 + 시계열 집계
│   ├── ratelimit.py              Claude 호출 엔드포인트 사용자별 시간당 크레딧 한도
│   ├── models/                   승격된 LightGBM 아티팩트(*.joblib) — 커밋 대상
│   ├── schemas.py                요청·응답 모델 (web/src/types/api.ts 와 1:1)
│   ├── scripts/create_user.py    운영자용 계정 생성
│   └── README.md
├── ml/                     LightGBM 생존 안정성 모델 (PRD §14~§15)
│   ├── labels.py                 Label 정의 (PRD §7.1)
│   ├── features.py               Feature 목록
│   ├── train.py                  Temporal Split 학습·평가 (--train-start-quarter로 학습 구간 제외 가능)
│   └── README.md
└── web/                    React 19 + TypeScript + Tailwind 4 프론트엔드
    ├── src/Root.tsx        로그인 관문 · 공개 페이지(/privacy) 분기
    ├── src/App.tsx         메인 화면 — 조건 state 소유 · API 호출 조립
    ├── src/components/
    │   ├── ui/             자체 디자인 시스템 (Radix 기반)
    │   ├── auth/           로그인 · 가입 · 비밀번호 재설정 · 소셜 버튼 · 계정 메뉴
    │   ├── detail/         상권 상세 드로어 (진단 · 점수 구성 · AI 근거)
    │   ├── charts/         시계열 · 경쟁강도 차트
    │   ├── tabs/           추천 · 물어보기 · 리포트 · 기록 (주요 탭 4개)
    │   ├── legal/          개인정보처리방침
    │   ├── RankMap.tsx     카카오맵 — 면적 비례 원 + 순위 배지 + 호버 툴팁
    │   └── OnboardingChat.tsx / ChatLog.tsx   자연어 조건 입력 (첫 방문 전체화면 → 사이드바로 축소)
    ├── src/lib/            api.ts(백엔드 유일 접점) · supabase.ts · auth.tsx · kakaoMaps.ts · conditionsStorage.ts · historyStorage.ts
    ├── src/types/          api.ts(backend/schemas.py 와 1:1) · 도메인 · UI 어휘
    ├── src/styles/         디자인 토큰 3계층 (재료 → 역할 → Tailwind)
    └── README.md           구조 · 로그인 · 토큰 추가 방법 · 접근성 규칙
```

---

## 빠른 시작

### 웹 화면 보기 (백엔드가 필요하다)

```bash
# 1) 백엔드 — 수집된 데이터가 있어야 한다
cd backend && uvicorn main:app --reload --port 8000

# 2) 웹
cd web && npm install && npm run dev   # http://localhost:5173
```

조건을 바꾸면 서버가 서울 전체를 다시 랭킹하고, 상권 행을 누르면 진단 4영역과
시계열이 담긴 상세 패널이 열린다. 추천·반대 근거는 Claude 호출이라 버튼을
눌러야 생성된다(15~20초, 과금).

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
상권 좌표/구/면적은 별도 API인 "영역-상권"(TbgisTrdarRelm)에서 가져온다 — 중심점
좌표와 면적만 제공하며 다각형 경계는 아니다.

2026-07-03 서울시 제공 기준 변경을 반영해 **2021년 이후 데이터만** 사용한다.
현재 2021Q1~2025Q2(18개 분기)를 수집했고, 외식업(CS1) 10개 세부업종 중
9개(한식·중식·일식·양식·제과점·치킨·분식·호프-간이주점·커피-음료 — 패스트푸드점
제외, Supabase 무료 티어 저장 용량 여유를 남기기 위한 선택) + 그 외 생활밀접업종
일부를 담고 있다.

보증금·임대료는 여전히 미보유다. 한국부동산원 R-ONE(부동산통계정보시스템)의
"상업용부동산 임대동향조사"에 실제 보증금/임대료 데이터가 있지만, 전국 368개
"대표 상권" 단위라 서울시 자체의 1,638개 골목상권(`상권_코드`) 체계보다 훨씬
거칠어서 — 정밀도를 지어내지 않기 위해 편입하지 않기로 했다.

Score는 개별 점포 생존확률이 아니라 **상권 × 업종 단위**의 폐업위험/생존 안정성
지표다. 개인 점포 생존확률로 해석하지 않는다.
