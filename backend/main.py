#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — FastAPI 백엔드
PRD §19 기술 스택 / §24 MVP Workflow의 "FastAPI 백엔드" 자리.

    Feature(district_features)
        -> /rank            (scoring.py, 결정론적 — 나중에 LightGBM으로 교체)
        -> /districts/{code}/agents  (fact_sheet.py + narrative_agents.py, Claude)
        -> /report          (report.py, 위 두 결과를 Top-K만큼 모아 PDF로, PRD F-15)

이 파일은 새 로직을 만들지 않는다 — alley_compass_etl/ 의 verification_tools.py,
fact_sheet.py, narrative_agents.py를 그대로 재사용한다. 별도 패키지로 만들지
않고 sys.path에 그 폴더를 끼워 넣는 이유는, 이미 있는 검증 로직을 복붙하지
않고 한 곳에서만 관리하기 위해서다 (PRD 원칙: 숫자는 코드가 증명한다 —
그 코드가 두 군데 있으면 안 된다).

실행:
    cd backend
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
ETL_DIR = BACKEND_DIR.parent / "alley_compass_etl"
sys.path.insert(0, str(ETL_DIR))

import anthropic  # noqa: E402
import pandas as pd  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import Response  # noqa: E402

from condition_parser import parse_condition  # noqa: E402
from fact_sheet import build_fact_sheet  # noqa: E402
from narrative_agents import DEFAULT_MODEL, generate_recommendation, generate_risk, verify_and_correct  # noqa: E402
from verification_tools import (  # noqa: E402
    ToolError,
    get_supabase_client,
    load_district_history,
    load_feature_frame,
    log,
)

from auth import CurrentUser, require_user  # noqa: E402
from detail import build_detail  # noqa: E402
from ratelimit import (  # noqa: E402
    AGENT_CREDIT_LIMIT_PER_HOUR,
    PARSE_CREDIT_LIMIT_PER_HOUR,
    RateLimitExceeded,
    charge,
)

from schemas import (  # noqa: E402
    AgentRequest,
    AgentResponse,
    DetailResponse,
    DistrictScore,
    ParseConditionRequest,
    ParseConditionResponse,
    RankRequest,
    RankResponse,
    ReportRequest,
    VerifiedClaimOut,
)
from scoring import MODEL_VERSION, active_model_version, rank_districts  # noqa: E402

load_dotenv(ETL_DIR / ".env")

app = FastAPI(
    title="골목 컴퍼스 API",
    description="서울 골목상권을 조건 기반으로 탐색·랭킹·검증하는 백엔드 (PRD §19)",
    version="0.1.0",
)
# 로그인 이후로는 Authorization 헤더가 오간다. 허용 출처는 항상 명시한 주소뿐이다 —
# "*" 나 포트 와일드카드를 두면 배포 환경에 그대로 나가기 쉽다.
#
#   배포       CORS_ORIGINS 에 실제 웹 주소를 적는다 (쉼표로 여러 개)
#   로컬 개발  기본값 http://localhost:5173 하나. Vite 는 strictPort 로 이 포트에만 뜨고,
#              Supabase·Google·카카오에 등록한 주소도 이것 하나다.
DEFAULT_CORS_ORIGINS = "http://localhost:5173"
CORS_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

_FRAME_CACHE: pd.DataFrame | None = None
_FRAME_CACHE_LOADED_AT: float | None = None
_USE_SUPABASE = os.getenv("BACKEND_USE_SUPABASE", "false").strip().lower() == "true"


def _frame_cache_ttl_seconds() -> int:
    default = 6 * 3600  # 6시간 — ETL은 분기(3개월)마다 도는데, 그보다 훨씬 촘촘히 확인할
    # 이유는 없다. 다만 "몇 시간 안엔 새로 올린 데이터가 반영된다"는 보장은 주고 싶어서
    # 하루보단 짧게 잡았다.
    raw = os.getenv("FRAME_CACHE_TTL_SECONDS", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


FRAME_CACHE_TTL_SECONDS = _frame_cache_ttl_seconds()


def get_frame(refresh: bool = False) -> pd.DataFrame:
    """district_features의 "최신 분기 한 개"를 메모리에 캐시해서 매 요청마다
    다시 읽지 않는다 — /rank·/agents·/report처럼 서울 전체 상권을 한 시점
    기준으로 비교하는 용도는 이걸로 충분하다.

    데이터 소스는 BACKEND_USE_SUPABASE(.env)로 고른다 — 기본은 로컬 CSV라
    Supabase service_role 권한(grant) 설정 전에도 API가 바로 동작한다.

    18개 분기(14만 행 이상) 전체를 올리지 않는 이유: 실제로 Render 무료
    플랜(512MB)에서 전체를 캐싱했다가 731MB까지 치솟아 메모리 초과로
    죽는 걸 확인했다. 과거 분기가 필요한 화면(상권 하나의 추이 차트)은
    load_district_history()로 그때그때 작게 따로 받는다 — district_detail()
    참고.

    캐시는 그냥 두면 프로세스가 사는 동안 영원히 안 바뀐다(예전엔 실제로
    그랬다) — alley_compass_etl.py로 새 분기를 Supabase에 올려도, 서버를
    수동 재시작하기 전까진 화면에 반영되지 않았다. Render 무료 플랜은 15분
    유휴면 재워서 우연히 매번 새로 읽혔을 뿐, 유료 플랜으로 올리거나
    트래픽이 끊이지 않으면 이 문제가 그대로 드러난다. 그래서 캐시가
    FRAME_CACHE_TTL_SECONDS(기본 6시간)보다 오래됐으면 다음 요청에서
    자동으로 다시 읽는다. 갱신이 실패해도(Supabase 일시 오류 등) 이미 있는
    캐시로 계속 서비스한다 — 캐시가 몇 시간 더 오래된 것과, 있던 서비스가
    아예 죽는 것 중 후자가 훨씬 나쁘다.
    """
    global _FRAME_CACHE, _FRAME_CACHE_LOADED_AT
    now = time.monotonic()

    if _FRAME_CACHE is None or refresh:
        try:
            _FRAME_CACHE = load_feature_frame(use_supabase=_USE_SUPABASE, latest_only=_USE_SUPABASE)
        except ToolError as exc:
            # 데이터가 아직 없는 상태(ETL 미실행 · Supabase 미적재).
            source = "Supabase district_features" if _USE_SUPABASE else "로컬 CSV"
            log(f"district_features 로드 실패 ({source}): {exc}")
            raise HTTPException(
                status_code=503,
                detail=(
                    f"분석할 상권 데이터가 아직 없습니다({source}). "
                    "alley_compass_etl.py 로 데이터를 수집하거나, 데이터가 올라간 Supabase 를 쓰려면 "
                    ".env 에 BACKEND_USE_SUPABASE=true 를 설정하세요."
                ),
            ) from exc
        except Exception as exc:  # noqa: BLE001 — Supabase 쪽 일시적 오류(타임아웃 등)
            # 위 ToolError는 "설정이 안 됐다"는 뜻이고, 이건 "설정은 맞는데 이번
            # 조회가 일시적으로 실패했다"는 뜻이다(예: Supabase statement timeout).
            # 500/502를 그대로 흘리면 CORS 헤더도 없이 죽어서 브라우저가 원인
            # 문구를 못 읽는다 — 503 + 재시도 안내로 감싼다.
            log(f"district_features 로드 중 일시적 오류: {exc}")
            raise HTTPException(
                status_code=503,
                detail="상권 데이터를 불러오는 중 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            ) from exc
        _FRAME_CACHE_LOADED_AT = now
        log(f"district_features 로드: {len(_FRAME_CACHE):,}행 (source={'supabase' if _USE_SUPABASE else 'csv'})")
    elif _FRAME_CACHE_LOADED_AT is not None and now - _FRAME_CACHE_LOADED_AT > FRAME_CACHE_TTL_SECONDS:
        try:
            fresh = load_feature_frame(use_supabase=_USE_SUPABASE, latest_only=_USE_SUPABASE)
        except Exception as exc:  # noqa: BLE001 — 갱신 실패는 기존 캐시로 넘어간다(위 설명 참고)
            # 다음 요청마다 다시 시도하며 Supabase를 두드리지 않도록, 실패해도
            # 시각은 갱신해 다음 시도까지 최소 TTL만큼 간격을 둔다.
            _FRAME_CACHE_LOADED_AT = now
            log(f"district_features 캐시 갱신 실패, 기존 캐시로 계속 서비스({exc})")
        else:
            _FRAME_CACHE = fresh
            _FRAME_CACHE_LOADED_AT = now
            log(f"district_features 캐시 갱신: {len(_FRAME_CACHE):,}행")

    return _FRAME_CACHE


_BUSINESS_TYPES_CACHE: pd.DataFrame | None = None


def get_business_types(refresh: bool = False) -> pd.DataFrame:
    """business_code/business_name 목록만 필요할 때 쓴다.

    Supabase 모드에서는 business_types 테이블(현재 6행)만 직접 조회한다.
    이 목록 하나 뽑자고 district_features(10만 행 이상)를 통째로 캐싱하는
    get_frame()을 부를 이유가 없다 — 대량 테이블 조회는 그 자체로 느리고
    (Render 무료 플랜의 제한된 CPU에서는 Supabase statement timeout까지
    난 적이 있다), /business-types·/parse-condition처럼 자주·가볍게 불리는
    엔드포인트를 매번 그 비용에 묶어 둘 필요가 없다.

    로컬 CSV 모드는 별도 마스터 테이블이 없으므로 기존처럼 캐시된
    feature 프레임에서 뽑는다(로컬 CSV는 어차피 가벼워 문제되지 않는다).
    """
    global _BUSINESS_TYPES_CACHE
    if _BUSINESS_TYPES_CACHE is None or refresh:
        if _USE_SUPABASE:
            supabase = get_supabase_client()
            rows = supabase.table("business_types").select("business_code,business_name").execute().data or []
            _BUSINESS_TYPES_CACHE = pd.DataFrame(rows)
        else:
            _BUSINESS_TYPES_CACHE = get_frame()[["business_code", "business_name"]]
        _BUSINESS_TYPES_CACHE = (
            _BUSINESS_TYPES_CACHE.dropna().drop_duplicates().sort_values("business_name").reset_index(drop=True)
        )
    return _BUSINESS_TYPES_CACHE


def _charge_or_429(bucket: str, user_id: str, credits: int, limit: int) -> None:
    """ratelimit.charge()를 부르고, 한도 초과면 429로 바꿔 던진다.
    프론트는 이 문구를 그대로 보여준다(web/src/lib/api.ts의 ApiError)."""
    try:
        charge(bucket, user_id, credits, limit)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=f"AI 호출이 너무 잦습니다. {exc.retry_after_seconds}초 후 다시 시도해 주세요.",
        ) from exc


def _none_if_nan(value):
    """pandas가 만든 결측값(NaN/NaT/pd.NA)을 Pydantic이 이해하는 파이썬 None으로 바꾼다.

    scoring.py의 rank_districts()가 이미 .where(notna(), None)으로 NaN을 None으로
    바꿔서 내보내는데도, 실제로 Render에서 특정 상권(geo 보강이 안 된 신규
    상권 등)에서 gu_name이 float NaN인 채로 DistrictScore(gu_name=...)에
    들어가 pydantic.ValidationError(string_type)를 내는 걸 직접 확인했다 —
    pandas 문자열 dtype 컬럼이 itertuples()를 거치면서 None이 다시 NaN으로
    바뀌는 경우가 있다. 그래서 Pydantic 모델을 만드는 이 마지막 지점에서
    한 번 더 확실히 None으로 정리한다.
    """
    return None if pd.isna(value) else value


def _condition_text(req: RankRequest | AgentRequest | ReportRequest) -> str:
    age_label = {"20": "20대", "30": "30대", "both": "20~30대"}[req.age]
    character_label = {
        "foot": "유동인구 중심", "resident": "주거 배후", "worker": "직장 배후", "campus": "대학가",
    }[req.character]
    priority_label = {"survival": "생존 안정성", "cost": "예산 적합성", "growth": "성장 가능성"}[req.priority]
    budget_part = f"보증금 {req.budget:,.0f}만원 이하 · " if req.budget else ""
    return f"{budget_part}타깃 {age_label} · 상권 성격: {character_label} · 우선순위: {priority_label}"


def _persist_run(
    req: RankRequest,
    ranked: pd.DataFrame,
    business_name: str,
    elapsed_ms: int,
    user_id: str,
) -> str | None:
    """Supabase에 search_sessions/recommendation_runs/recommendations를 기록한다.

    실패해도 /rank 응답 자체는 막지 않는다 — 기록은 부가 기능이지 핵심 경로가
    아니다(PRD §22 Data Flywheel용 축적이지, 지금 당장 응답에 필요한 값이 아님).
    """
    try:
        supabase = get_supabase_client()

        biz = supabase.table("business_types").select("id").eq("business_code", req.business_code).limit(1).execute()
        if not biz.data:
            return None
        business_type_id = biz.data[0]["id"]

        session = (
            supabase.table("search_sessions")
            .insert({
                "user_id": user_id,
                "business_type_id": business_type_id,
                "budget": req.budget,
                "target_age": req.age,
                "market_character": req.character,
                "priority": req.priority,
            })
            .execute()
        )
        session_id = session.data[0]["id"]

        run = (
            supabase.table("recommendation_runs")
            .insert({
                "session_id": session_id,
                "conditions": req.model_dump(),
                "execution_time_ms": elapsed_ms,
            })
            .execute()
        )
        run_id = run.data[0]["id"]

        codes = ranked.head(req.top_k)["district_code"].tolist()
        districts = (
            supabase.table("districts").select("id,district_code").in_("district_code", codes).execute()
        )
        district_map = {d["district_code"]: d["id"] for d in districts.data}

        rows = []
        for r in ranked.head(req.top_k).itertuples():
            district_id = district_map.get(r.district_code)
            if district_id is None:
                continue
            rows.append({
                "run_id": run_id,
                "district_id": district_id,
                "rank": int(r.rank),
                "stability_score": float(r.stability_score),
                "final_score": float(r.final_score),
                "target_fit_score": float(r.target_fit_score),
                "score_breakdown": r.breakdown,
            })
        if rows:
            supabase.table("recommendations").insert(rows).execute()

        return str(run_id)
    except Exception as exc:  # noqa: BLE001 — 기록 실패는 로그만 남기고 무시
        log(f"[경고] 세션 기록 실패(핵심 응답에는 영향 없음): {exc}")
        return None


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model_version": active_model_version(),
        "data_source": "supabase" if _USE_SUPABASE else "csv",
    }


@app.get("/business-types")
def business_types(_user: CurrentUser = Depends(require_user)) -> list[dict]:
    return get_business_types().to_dict(orient="records")


@app.post("/parse-condition", response_model=ParseConditionResponse)
def parse_condition_endpoint(
    req: ParseConditionRequest,
    user: CurrentUser = Depends(require_user),
) -> ParseConditionResponse:
    """ConditionBar를 대체하는 자연어 입력. PRD F-12 대화형 재탐색.

    직전 조건(req.previous)을 같이 보내면 이번 문장에서 언급 안 한 필드는
    그대로 유지된다 — 매번 처음부터 다시 묻지 않는다. business_code는
    Claude가 뭐라 답하든 실제 수집된 업종 목록으로 다시 확인한다
    (condition_parser.parse_condition 안에서 처리).
    """
    _charge_or_429("parse", user.user_id, 1, PARSE_CREDIT_LIMIT_PER_HOUR)
    biz_df = get_business_types()
    businesses = biz_df.to_dict(orient="records")

    client = anthropic.Anthropic()
    try:
        parsed = parse_condition(client, req.message, req.previous.model_dump(), businesses)
    except anthropic.AuthenticationError as exc:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY가 설정되지 않았거나 잘못됐습니다.") from exc
    except anthropic.RateLimitError as exc:
        raise HTTPException(status_code=429, detail=f"Claude API rate limit: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API 오류 ({exc.status_code}): {exc.message}") from exc

    business_name = None
    if parsed.business_code is not None:
        match = biz_df.loc[biz_df["business_code"] == parsed.business_code, "business_name"]
        business_name = match.iloc[0] if not match.empty else None

    return ParseConditionResponse(
        business_code=parsed.business_code,
        business_name=business_name,
        business_not_found=parsed.business_not_found,
        budget=parsed.budget,
        age=parsed.age,
        character=parsed.character,
        priority=parsed.priority,
    )


@app.get("/districts")
def districts(
    business_code: str | None = None,
    _user: CurrentUser = Depends(require_user),
) -> list[dict]:
    df = get_frame()
    scope = df if business_code is None else df[df["business_code"] == business_code]
    rows = scope[["district_code", "district_name"]].dropna().drop_duplicates().sort_values("district_code")
    return rows.to_dict(orient="records")


@app.post("/rank", response_model=RankResponse)
def rank(req: RankRequest, user: CurrentUser = Depends(require_user)) -> RankResponse:
    """PRD §16 개인화 Ranking. 서울 전체(해당 업종 데이터가 있는 상권 전부)를 재랭킹한다."""
    df = get_frame()
    started = time.monotonic()
    try:
        ranked, as_of = rank_districts(df, req.business_code, age=req.age, character=req.character, priority=req.priority)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    elapsed_ms = int((time.monotonic() - started) * 1000)

    business_name = df.loc[df["business_code"] == req.business_code, "business_name"].iloc[0]
    run_id = _persist_run(req, ranked, business_name, elapsed_ms, user.user_id)

    missing = ranked.attrs.get("missing_components", [])
    label = {"perf": "매출 성장률", "stability": "폐업 추세", "access": "교통·집객 접근성", "comp": "경쟁강도", "demand": "수요"}
    warnings = [f"{label.get(k, k)} 데이터가 부족해 이번 랭킹에서 제외했습니다." for k in missing]

    top = ranked.head(req.top_k)
    return RankResponse(
        run_id=run_id,
        business_code=req.business_code,
        business_name=business_name,
        as_of=as_of,
        n_candidates=len(ranked),
        model_version=ranked.attrs.get("model_version", MODEL_VERSION),
        warnings=warnings,
        results=[
            DistrictScore(
                rank=int(r.rank),
                district_code=r.district_code,
                district_name=r.district_name,
                stability_score=r.stability_score,
                target_fit_score=r.target_fit_score,
                final_score=r.final_score,
                score_breakdown=r.breakdown,
                gu_name=_none_if_nan(r.gu_name),
                latitude=_none_if_nan(r.latitude),
                longitude=_none_if_nan(r.longitude),
                area_m2=_none_if_nan(r.area_m2),
            )
            for r in top.itertuples()
        ],
    )


@app.get("/districts/{district_code}/detail", response_model=DetailResponse)
def district_detail(
    district_code: str,
    business_code: str,
    _user: CurrentUser = Depends(require_user),
) -> DetailResponse:
    """PRD §17.4 상세 Drawer 가 쓰는 상권 진단 4영역 + 시계열.

    /agents 와 달리 Claude 를 호출하지 않는다 — 전부 Pandas 집계라 과금이
    없고, 그래서 상권을 열 때마다 바로 불러도 된다. 백분위 정의는
    verification_tools.percentile() 을 그대로 쓴다.

    df(캐시, 최신 분기 전체 상권)와 history(이 상권×업종 하나의 전체 이력,
    그때그때 작게 조회)를 분리해서 build_detail()에 같이 넘긴다 — 추이
    차트만 과거 분기가 필요하고, 나머지 진단은 최신 분기 비교로 충분하다.
    """
    df = get_frame()
    history = load_district_history(district_code, business_code, use_supabase=_USE_SUPABASE)
    try:
        return DetailResponse(**build_detail(df, history, district_code, business_code))
    except ToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/districts/{district_code}/agents", response_model=AgentResponse)
def district_agents(
    district_code: str,
    req: AgentRequest,
    user: CurrentUser = Depends(require_user),
) -> AgentResponse:
    """PRD §10 Recommendation/Risk/Verification Agent 체인.

    Claude API를 실제로 호출하므로 과금이 발생한다 — /rank와 분리된 별도
    엔드포인트로 둔 이유다.
    """
    _charge_or_429("agent", user.user_id, 2, AGENT_CREDIT_LIMIT_PER_HOUR)
    df = get_frame()
    biz_rows = df[df["business_code"] == req.business_code]
    row = biz_rows[biz_rows["district_code"] == str(district_code)]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"상권_코드 {district_code} × 업종 {req.business_code} 데이터가 없습니다.")

    district_name = row["district_name"].iloc[0]
    business_label = row["business_name"].iloc[0]

    try:
        facts = build_fact_sheet(df, district_code, req.business_code, budget=req.budget, estimated_deposit=req.estimated_deposit)
    except ToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    client = anthropic.Anthropic()
    model = req.model or DEFAULT_MODEL
    conditions_text = _condition_text(req)

    try:
        rec_output = generate_recommendation(client, district_name, business_label, conditions_text, facts, model=model)
        risk_output = generate_risk(client, district_name, business_label, conditions_text, facts, model=model)
        rec_verified = verify_and_correct(client, rec_output.claims, facts, model=model)
        risk_verified = verify_and_correct(client, risk_output.claims, facts, model=model)
    except anthropic.AuthenticationError as exc:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY가 설정되지 않았거나 잘못됐습니다.") from exc
    except anthropic.RateLimitError as exc:
        raise HTTPException(status_code=429, detail=f"Claude API rate limit: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API 오류 ({exc.status_code}): {exc.message}") from exc

    def to_out(claims) -> list[VerifiedClaimOut]:
        return [
            VerifiedClaimOut(
                claim_text=c.claim_text, fact_key=c.fact_key, claimed_value=c.claimed_value,
                actual_value=c.actual_value, tolerance=c.tolerance, verified=c.verified,
                verification_tool=c.verification_tool, verification_reason=c.verification_reason,
                corrected=c.corrected,
            )
            for c in claims
        ]

    return AgentResponse(
        district_code=district_code,
        district_name=district_name,
        business_label=business_label,
        facts={k: {"label": f.label, "value": f.value, "unit": f.unit} for k, f in facts.items()},
        recommendation=to_out(rec_verified),
        risk=to_out(risk_verified),
    )


@app.post("/report")
def report(req: ReportRequest, user: CurrentUser = Depends(require_user)) -> Response:
    """PRD F-15. Top-K 상권 + 각 상권의 추천/반대 근거를 PDF 한 장으로 묶는다.

    상권마다 Recommendation + Risk Agent를 호출하므로(최대 10곳 x 2회) 응답까지
    수십 초~분 단위가 걸리고 실제 과금이 발생한다 — top_k를 10 이하로 제한한
    이유다. 개별 상권에서 Claude 호출이 실패해도 그 상권만 오류를 표시하고
    나머지는 계속 진행한다(전체 리포트가 한 상권 때문에 실패하지 않도록).
    """
    # top_k 만큼 agents 를 반복 호출하는 것과 같은 비용이라 크레딧도 그만큼 문다.
    # WeasyPrint 를 확인하기 전에 먼저 검사한다 — 한도를 넘겼는데 "PDF 라이브러리가
    # 없다"는 무관한 오류부터 보이면 진짜 원인을 못 찾는다.
    _charge_or_429("agent", user.user_id, req.top_k * 2, AGENT_CREDIT_LIMIT_PER_HOUR)

    # report.py 는 WeasyPrint 를 import 하고, WeasyPrint 는 그 순간 Pango/GTK
    # 시스템 라이브러리를 불러온다. 모듈 맨 위에서 import 하면 이 라이브러리가
    # 없는 PC(Windows 기본 상태)에서 서버 전체가 뜨지 못한다. PDF 는 부가 기능이라
    # 여기서만 불러오고, Claude 를 부르기 전에 확인해 과금 후 실패하는 일을 막는다.
    try:
        from report import build_report_pdf
    except (ImportError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "PDF 생성 라이브러리(WeasyPrint)를 불러올 수 없습니다. "
                "backend/README.md 의 설치 안내를 확인하세요."
            ),
        ) from exc

    df = get_frame()
    try:
        ranked, as_of = rank_districts(df, req.business_code, age=req.age, character=req.character, priority=req.priority)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    biz_rows = df[df["business_code"] == req.business_code]
    business_name = biz_rows["business_name"].iloc[0]
    conditions_text = _condition_text(req)
    model = req.model or DEFAULT_MODEL
    client = anthropic.Anthropic()

    sections = []
    for r in ranked.head(req.top_k).itertuples():
        row = biz_rows[biz_rows["district_code"] == r.district_code]
        district_name = row["district_name"].iloc[0]
        rec_claims: list[dict] = []
        risk_claims: list[dict] = []
        agent_error: str | None = None
        try:
            facts = build_fact_sheet(df, r.district_code, req.business_code, budget=req.budget)
            rec_output = generate_recommendation(client, district_name, business_name, conditions_text, facts, model=model)
            risk_output = generate_risk(client, district_name, business_name, conditions_text, facts, model=model)
            rec_claims = [
                {"text": c.claim_text, "corrected": c.corrected}
                for c in verify_and_correct(client, rec_output.claims, facts, model=model)
                if c.verified
            ]
            risk_claims = [
                {"text": c.claim_text, "corrected": c.corrected}
                for c in verify_and_correct(client, risk_output.claims, facts, model=model)
                if c.verified
            ]
        except ToolError as exc:
            agent_error = str(exc)
        except anthropic.AuthenticationError:
            agent_error = "ANTHROPIC_API_KEY가 설정되지 않았거나 잘못됐습니다."
        except anthropic.RateLimitError as exc:
            agent_error = f"Claude API rate limit: {exc}"
        except anthropic.APIStatusError as exc:
            agent_error = f"Claude API 오류 ({exc.status_code}): {exc.message}"

        sections.append({
            "rank": int(r.rank),
            "district_code": r.district_code,
            "district_name": district_name,
            "final_score": float(r.final_score),
            "breakdown": r.breakdown,
            "recommendation": rec_claims,
            "risk": risk_claims,
            "agent_error": agent_error,
        })

    pdf_bytes = build_report_pdf(
        business_name=business_name,
        conditions_text=conditions_text,
        as_of=as_of,
        model_version=ranked.attrs.get("model_version", MODEL_VERSION),
        sections=sections,
    )
    filename = f"alley-compass-{req.business_code}-{datetime.now():%Y%m%d%H%M}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
