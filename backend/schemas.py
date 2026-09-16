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


class AgentResponse(BaseModel):
    district_code: str
    district_name: str
    business_label: str
    facts: dict[str, dict]
    recommendation: list[VerifiedClaimOut]
    risk: list[VerifiedClaimOut]
