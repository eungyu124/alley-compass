#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — Fact Sheet Builder
PRD §9 Data/Prediction Layer 와 Recommendation/Risk Agent 사이의 접합부.

역할
    Recommendation/Risk Agent(Claude)에게 "이 숫자들만 문장으로 바꿔라"고
    건네줄 사실(Fact) 목록을 결정론적 코드로 만든다.

    Claude는 새 숫자를 계산하지 않는다 — 여기 있는 값만 골라 자연어로
    바꾸고, 어떤 Fact를 근거로 썼는지와 문장에 실제로 적은 수치를 함께
    반환한다. Verification Agent(narrative_agents.verify_and_correct)는
    그 수치가 Fact의 실제 값과 일치하는지만 확인하면 된다.

    PRD §1: "집계 연산은 전부 Pandas 코드가 수행하며 AI를 사용하지
    않음 — LLM은 수치 집계에서 오류가 발생하기 때문"의 실제 구현.

한 Fact는 정확히 하나의 숫자를 담는다(문장 하나 = Fact 하나 대응).
데이터가 없으면(분기 부족, 조회 실패 등) 그 Fact는 만들지 않는다 —
없는 값을 지어내 Agent에게 건네주지 않기 위함이다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from verification_tools import (
    ToolError,
    budget_validator,
    competition_density,
    data_lookup,
    percentile,
    trend,
)


@dataclass
class Fact:
    key: str
    label: str
    value: float
    unit: str
    tolerance: float


# (metric, key, label, tolerance) — 전부 "상위 N%" 백분위 프레이밍.
# 값이 클수록 백분위가 낮아지는 지표(폐업률 등)라도 상위 N%가 곧
# "그 지표가 서울에서 가장 높은 축에 속한다"는 뜻이므로 해석은 그대로
# 유효하다. 좋은 소식으로 읽을지 나쁜 소식으로 읽을지는 Agent가
# stance(추천/반대)에 맞게 판단한다.
_PERCENTILE_SPECS: list[tuple[str, str, str, float]] = [
    ("foot_traffic_20", "foot20_percentile", "20대 유동인구 백분위", 2.0),
    ("foot_traffic_30", "foot30_percentile", "30대 유동인구 백분위", 2.0),
    ("resident_population", "resident_percentile", "상주인구 백분위", 2.0),
    ("worker_population", "worker_percentile", "직장인구 백분위", 2.0),
    ("closure_rate", "closure_rate_percentile", "폐업률 백분위", 2.0),
    ("facility_count", "facility_percentile", "집객시설 수 백분위", 2.0),
]

# (metric, key, label, unit, tolerance, scale) — 원본 값을 그대로(또는
# 스케일만 바꿔) 전달하는 Fact. scale은 단위 변환용(예: 원 -> 만원).
_LOOKUP_SPECS: list[tuple[str, str, str, str, float, float]] = [
    ("estimated_sales", "estimated_sales_value", "추정매출(월)", "만원", 50.0, 1 / 10000),
    ("closure_rate", "closure_rate_value", "폐업률", "%", 1.0, 1.0),
    ("opening_rate", "opening_rate_value", "개업률", "%", 1.0, 1.0),
    ("transit_score", "transit_score_value", "교통 접근성 점수(서울 골목상권 내 백분위)", "점", 2.0, 1.0),
]


def build_fact_sheet(
    df: pd.DataFrame,
    district_code: str,
    business_code: str,
    *,
    budget: float | None = None,
    estimated_deposit: float | None = None,
) -> dict[str, Fact]:
    """한 상권×업종 조합에 대해 Agent에게 건넬 Fact 목록을 만든다.

    budget / estimated_deposit을 둘 다 주면 budget_margin Fact가
    추가된다. estimated_deposit은 5종 공식 데이터셋에 없는 외부
    추정치이므로(verification_tools.budget_validator 참고), 실제
    서비스에서는 부동산 시세 등 별도 출처에서 받아와야 한다.
    """
    facts: dict[str, Fact] = {}

    def add(key: str, label: str, value: float | None, unit: str, tolerance: float) -> None:
        if value is None:
            return
        facts[key] = Fact(key=key, label=label, value=round(float(value), 2), unit=unit, tolerance=tolerance)

    for metric, key, label, tol in _PERCENTILE_SPECS:
        try:
            p = percentile(df, metric, district_code, business_code)
            add(key, f"{label}(상위 N%)", p["top_pct"], "%", tol)
        except ToolError:
            continue

    try:
        c = competition_density(df, district_code, business_code)
        add("competition_ratio", "동종업종 점포수 / 서울 평균", c["ratio_to_avg"], "배", 0.15)
    except ToolError:
        pass

    try:
        t = trend(df, district_code, "estimated_sales", business_code, periods=4)
        if t["available"]:
            add("sales_trend_pct", "최근 4분기 추정매출 변화율", t["change_pct"], "%", 1.5)
    except ToolError:
        pass

    for metric, key, label, unit, tol, scale in _LOOKUP_SPECS:
        try:
            looked_up = data_lookup(df, district_code, metric, business_code)
        except ToolError:
            continue
        if looked_up["found"] and looked_up["value"] is not None:
            add(key, label, looked_up["value"] * scale, unit, tol)

    if budget is not None and estimated_deposit is not None:
        b = budget_validator(estimated_deposit, budget)
        add(
            "budget_margin",
            f"예산({budget:,.0f}만원) 대비 여유(추정 보증금 {estimated_deposit:,.0f}만원 기준)",
            b["margin"],
            "만원",
            50.0,
        )

    return facts


def render_fact_sheet(facts: dict[str, Fact]) -> str:
    """Agent 프롬프트에 넣을 사람이 읽는 목록 텍스트."""
    if not facts:
        return "(사용 가능한 Fact 없음)"
    return "\n".join(f"- {f.key}: {f.label} = {f.value}{f.unit}" for f in facts.values())


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    from verification_tools import load_feature_frame, log, pick_demo_target

    p = argparse.ArgumentParser(description="Fact Sheet 빌더 데모")
    p.add_argument("--csv", type=Path, default=None)
    p.add_argument("--supabase", action="store_true")
    p.add_argument("--district-code", default=None)
    p.add_argument("--business-code", default=None)
    p.add_argument("--budget", type=float, default=5000)
    p.add_argument("--estimated-deposit", type=float, default=6200)
    args = p.parse_args()

    frame = load_feature_frame(csv_path=args.csv, use_supabase=args.supabase)
    biz, dist, _ = pick_demo_target(frame, args.business_code, args.district_code)

    sheet = build_fact_sheet(
        frame, dist, biz, budget=args.budget, estimated_deposit=args.estimated_deposit
    )
    log(f"Fact Sheet ({len(sheet)}개):")
    print(render_fact_sheet(sheet))
