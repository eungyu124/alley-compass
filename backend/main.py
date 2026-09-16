#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — FastAPI 백엔드
PRD §19 기술 스택 / §24 MVP Workflow의 "FastAPI 백엔드" 자리.

    Feature(district_features)
        -> /rank            (scoring.py, 결정론적 — 나중에 LightGBM으로 교체)
        -> /districts/{code}/agents  (fact_sheet.py + narrative_agents.py, Claude)

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
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
ETL_DIR = BACKEND_DIR.parent / "alley_compass_etl"
sys.path.insert(0, str(ETL_DIR))

import anthropic  # noqa: E402
import pandas as pd  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from fact_sheet import build_fact_sheet  # noqa: E402
from narrative_agents import DEFAULT_MODEL, generate_recommendation, generate_risk, verify_and_correct  # noqa: E402
from verification_tools import ToolError, get_supabase_client, load_feature_frame, log  # noqa: E402

from schemas import (  # noqa: E402
    AgentRequest,
    AgentResponse,
    DistrictScore,
    RankRequest,
    RankResponse,
    VerifiedClaimOut,
)
from scoring import MODEL_VERSION, rank_districts  # noqa: E402

load_dotenv(ETL_DIR / ".env")

app = FastAPI(
    title="골목 컴퍼스 API",
    description="서울 골목상권을 조건 기반으로 탐색·랭킹·검증하는 백엔드 (PRD §19)",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRAME_CACHE: pd.DataFrame | None = None
_USE_SUPABASE = os.getenv("BACKEND_USE_SUPABASE", "false").strip().lower() == "true"


def get_frame(refresh: bool = False) -> pd.DataFrame:
    """district_features를 메모리에 캐시해서 매 요청마다 다시 읽지 않는다.

    데이터 소스는 BACKEND_USE_SUPABASE(.env)로 고른다 — 기본은 로컬 CSV라
    Supabase service_role 권한(grant) 설정 전에도 API가 바로 동작한다.
    """
    global _FRAME_CACHE
    if _FRAME_CACHE is None or refresh:
        _FRAME_CACHE = load_feature_frame(use_supabase=_USE_SUPABASE)
        log(f"district_features 로드: {len(_FRAME_CACHE):,}행 (source={'supabase' if _USE_SUPABASE else 'csv'})")
    return _FRAME_CACHE


def _condition_text(req: RankRequest | AgentRequest) -> str:
    age_label = {"20": "20대", "30": "30대", "both": "20~30대"}[req.age]
    character_label = {
        "foot": "유동인구 중심", "resident": "주거 배후", "worker": "직장 배후", "campus": "대학가",
    }[req.character]
    priority_label = {"survival": "생존 안정성", "cost": "예산 적합성", "growth": "성장 가능성"}[req.priority]
    budget_part = f"보증금 {req.budget:,.0f}만원 이하 · " if req.budget else ""
    return f"{budget_part}타깃 {age_label} · 상권 성격: {character_label} · 우선순위: {priority_label}"


def _persist_run(req: RankRequest, ranked: pd.DataFrame, business_name: str, elapsed_ms: int) -> str | None:
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
    return {"status": "ok", "model_version": MODEL_VERSION, "data_source": "supabase" if _USE_SUPABASE else "csv"}


@app.get("/business-types")
def business_types() -> list[dict]:
    df = get_frame()
    rows = df[["business_code", "business_name"]].dropna().drop_duplicates().sort_values("business_name")
    return rows.to_dict(orient="records")


@app.get("/districts")
def districts(business_code: str | None = None) -> list[dict]:
    df = get_frame()
    scope = df if business_code is None else df[df["business_code"] == business_code]
    rows = scope[["district_code", "district_name"]].dropna().drop_duplicates().sort_values("district_code")
    return rows.to_dict(orient="records")


@app.post("/rank", response_model=RankResponse)
def rank(req: RankRequest) -> RankResponse:
    """PRD §16 개인화 Ranking. 서울 전체(해당 업종 데이터가 있는 상권 전부)를 재랭킹한다."""
    df = get_frame()
    started = time.monotonic()
    try:
        ranked, as_of = rank_districts(df, req.business_code, age=req.age, character=req.character, priority=req.priority)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    elapsed_ms = int((time.monotonic() - started) * 1000)

    business_name = df.loc[df["business_code"] == req.business_code, "business_name"].iloc[0]
    run_id = _persist_run(req, ranked, business_name, elapsed_ms)

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
        model_version=MODEL_VERSION,
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
            )
            for r in top.itertuples()
        ],
    )


@app.post("/districts/{district_code}/agents", response_model=AgentResponse)
def district_agents(district_code: str, req: AgentRequest) -> AgentResponse:
    """PRD §10 Recommendation/Risk/Verification Agent 체인.

    Claude API를 실제로 호출하므로 과금이 발생한다 — /rank와 분리된 별도
    엔드포인트로 둔 이유다.
    """
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
