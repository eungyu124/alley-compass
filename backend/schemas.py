#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""골목 컴퍼스 API — 요청/응답 스키마 (PRD §17 화면과 1:1 대응)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RankRequest(BaseModel):
    business_code: str = Field(description="업종 코드 (예: CS100010)")
    budget: Optional[float] = Field(default=None, description="보증금 예산(만원). 세션 기록용 — 랭킹 계산에는 아직 반영되지 않음(§16 참고)")
    age: Literal["20", "30", "both"] = "both"
    character: Literal["foot", "resident", "worker", "campus"] = "foot"
    priority: Literal["survival", "cost", "growth"] = "survival"
    top_k: int = Field(default=5, ge=1, le=50)


class DistrictScore(BaseModel):
    rank: int
    district_code: str
    district_name: str
    stability_score: float
    target_fit_score: float
    final_score: float
    score_breakdown: dict


class RankResponse(BaseModel):
    run_id: Optional[str] = Field(description="Supabase에 세션이 기록됐으면 recommendation_runs.id")
    business_code: str
    business_name: str
    as_of: str
    n_candidates: int
    model_version: str
    warnings: list[str] = Field(default_factory=list, description="이번 실행에서 데이터 부족으로 제외된 랭킹 요소 등")
    results: list[DistrictScore]


class AgentRequest(BaseModel):
    business_code: str
    budget: Optional[float] = None
    estimated_deposit: Optional[float] = Field(
        default=None,
        description="추정 보증금(만원, 외부 추정치). budget과 함께 주면 예산 여유 Fact가 생성됨",
    )
    age: Literal["20", "30", "both"] = "both"
    character: Literal["foot", "resident", "worker", "campus"] = "foot"
    priority: Literal["survival", "cost", "growth"] = "survival"
    model: Optional[str] = None


class VerifiedClaimOut(BaseModel):
    claim_text: str
    fact_key: str
    claimed_value: float
    actual_value: Optional[float]
    tolerance: Optional[float]
    verified: bool
    verification_tool: str
    verification_reason: str
    corrected: bool


class ReportRequest(BaseModel):
    business_code: str
    budget: Optional[float] = None
    age: Literal["20", "30", "both"] = "both"
    character: Literal["foot", "resident", "worker", "campus"] = "foot"
    priority: Literal["survival", "cost", "growth"] = "survival"
    top_k: int = Field(
        default=5, ge=1, le=10,
        description="상권당 Claude를 최대 2회(추천+반대) 호출하므로 과금·시간 보호를 위해 10 이하로 제한",
    )
    model: Optional[str] = None


class AgentResponse(BaseModel):
    district_code: str
    district_name: str
    business_label: str
    facts: dict[str, dict]
    recommendation: list[VerifiedClaimOut]
    risk: list[VerifiedClaimOut]


# ── 자연어 조건 파서 (POST /parse-condition) ────────────────────
# ConditionBar를 대체하는 대화형 입력. 직전 조건(previous)을 같이 보내면
# 문장에서 언급 안 한 필드는 그대로 유지된다 (매번 새로 파싱하지 않음).


class ConditionState(BaseModel):
    """직전 턴의 조건. 첫 대화면 전부 비워서(business_code=None 등) 보낸다."""

    business_code: Optional[str] = None
    budget: Optional[float] = None
    age: Literal["20", "30", "both"] = "both"
    character: Literal["foot", "resident", "worker", "campus"] = "foot"
    priority: Literal["survival", "cost", "growth"] = "survival"


class ParseConditionRequest(BaseModel):
    message: str = Field(description="사용자가 입력한 자유 문장")
    previous: ConditionState = Field(default_factory=ConditionState)


class ParseConditionResponse(BaseModel):
    business_code: Optional[str] = None
    business_name: Optional[str] = Field(
        default=None, description="business_code를 실제 업종명으로 변환한 값. 참고용"
    )
    """사용자가 언급했지만 수집된 업종 목록에 없는 이름. 있으면 프론트가 안내 문구를 띄운다."""
    business_not_found: Optional[str] = None
    budget: Optional[float] = None
    age: Literal["20", "30", "both"] = "both"
    character: Literal["foot", "resident", "worker", "campus"] = "foot"
    priority: Literal["survival", "cost", "growth"] = "survival"


# ── 상세 지표 (GET /districts/{code}/detail) ────────────────────
# 프론트의 상권 진단·차트가 쓰는 값. 전부 backend/detail.py 가 결정론적으로
# 집계한다 — 백분위 정의를 verification_tools.percentile() 하나로 유지하기
# 위해서다. Claude 를 부르지 않으므로 과금되지 않는다.


class DiagnosticRow(BaseModel):
    label: str
    value: str
    """서울 골목상권 내 상위 %. 비교 대상이 없으면 None."""
    top: Optional[float] = None
    """데이터로 검증되지 않은 외부 추정치인가"""
    unverified: bool = False


class DiagnosticArea(BaseModel):
    key: Literal["customers", "competition", "environment", "cost"]
    label: str
    caption: str
    """False 면 score/top_pct 가 None 이다 — 없는 데이터에 점수를 만들지 않는다."""
    available: bool
    score: Optional[int] = None
    top_pct: Optional[float] = None
    rows: list[DiagnosticRow]
    note: Optional[str] = None


class SeriesPoint(BaseModel):
    label: str
    value: float


class Series(BaseModel):
    available: bool
    """available=False 인 이유. 화면에 그대로 표시한다."""
    reason: Optional[str] = None
    points: list[SeriesPoint] = Field(default_factory=list)


class CompetitionChart(BaseModel):
    district_value: float
    city_avg: float
    store_count: int
    n_districts: int
    ratio_to_avg: Optional[float] = None


class DetailSeries(BaseModel):
    hourly: Series
    sales: Series
    closure: Series
    competition: Optional[CompetitionChart] = None


class DetailResponse(BaseModel):
    district_code: str
    district_name: str
    business_code: str
    business_name: str
    as_of: str
    """이 상권×업종에 실제로 수집된 분기 수. 1이면 추세 계열이 전부 비어 있다."""
    quarters_collected: int
    diagnostics: list[DiagnosticArea]
    series: DetailSeries
    metrics_available: list[str]
