#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — Label 정의
PRD §7.1 구현

PRD §7.1은 4개 후보를 제시하고 "EDA 이후 확정"이라고 열어 뒀다. 이 파일은
후보 ④(폐업률·매출 안정성 결합)에 후보 ①(다음 기간 폐업률 증가 여부)의
관점을 더해 채택한다 — 폐업률만 보면 원래 낮은 상권의 작은 변동에 과민하고,
매출만 보면 계절 변동에 흔들리므로 둘을 OR로 묶어 어느 한쪽만 나빠져도
잡아낸다.

정의
    시점 t의 상권×업종 관측치에 대해, t 이후 horizon_quarters 구간
    (t+1 ~ t+H)의 실적을 보고 label_unstable(0/1)을 매긴다.

        future_closure_rate = t+1..t+H 폐업률 평균
        future_sales_growth = t+1..t+H 매출 성장률 평균

        label_unstable = 1  if  future_closure_rate > 같은 시점·업종 상권들의
                                 중앙값 * closure_margin
                             or  future_sales_growth < sales_decline_threshold
                        = 0  otherwise

    "같은 시점·업종 중앙값 대비"로 잡는 이유: 업종마다 폐업률 절대 수준이
    다르므로(예: 배달 위주 업종 vs 오프라인 의존 업종) 고정 임계값보다
    상대 비교가 왜곡이 적다.

Look-ahead 방지
    feature는 시점 t 이전 값만 쓰고(features.py), label은 t 이후 값만 쓴다.
    이 함수는 그룹(상권×업종) 내부에서 미래로 shift한 값만 사용하며, 미래
    구간이 아예 없는 마지막 H개 분기는 label_available=False로 남기고
    라벨을 지어내지 않는다(추정으로 메우지 않는다 — 루트 README 설계원칙 3).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_HORIZON_QUARTERS = 4
DEFAULT_CLOSURE_MARGIN = 1.15
DEFAULT_SALES_DECLINE = -0.05

REQUIRED_COLUMNS = {"district_code", "business_code", "q_index", "closure_rate", "sales_growth_rate"}


def build_labels(
    panel: pd.DataFrame,
    horizon_quarters: int = DEFAULT_HORIZON_QUARTERS,
    closure_margin: float = DEFAULT_CLOSURE_MARGIN,
    sales_decline_threshold: float = DEFAULT_SALES_DECLINE,
) -> pd.DataFrame:
    """panel(상권×업종×분기 패널)에 label_unstable / label_available 컬럼을 붙여 돌려준다."""
    missing = REQUIRED_COLUMNS - set(panel.columns)
    if missing:
        raise ValueError(f"panel에 다음 컬럼이 없습니다: {sorted(missing)}")

    df = panel.sort_values(["district_code", "business_code", "q_index"]).reset_index(drop=True)
    grp = df.groupby(["district_code", "business_code"], sort=False)

    lead_cr_cols, lead_sg_cols = [], []
    for h in range(1, horizon_quarters + 1):
        cr_col, sg_col = f"_cr_lead{h}", f"_sg_lead{h}"
        df[cr_col] = grp["closure_rate"].shift(-h)
        df[sg_col] = grp["sales_growth_rate"].shift(-h)
        lead_cr_cols.append(cr_col)
        lead_sg_cols.append(sg_col)

    df["future_closure_rate"] = df[lead_cr_cols].mean(axis=1, skipna=True)
    df["future_sales_growth"] = df[lead_sg_cols].mean(axis=1, skipna=True)
    label_available = df[lead_cr_cols + lead_sg_cols].notna().any(axis=1)

    median_by_period = df.groupby(["business_code", "q_index"])["future_closure_rate"].transform("median")
    unstable = (df["future_closure_rate"] > median_by_period * closure_margin) | (
        df["future_sales_growth"] < sales_decline_threshold
    )

    df["label_unstable"] = np.where(label_available, unstable.astype(float), np.nan)
    df["label_available"] = label_available
    return df.drop(columns=lead_cr_cols + lead_sg_cols)
