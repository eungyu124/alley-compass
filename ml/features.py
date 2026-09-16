#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — Feature 목록
PRD §7.1 Input 구현. district_features 스키마 컬럼을 그대로 쓴다 —
LightGBM 입력을 만들려고 별도 feature store를 두지 않는다.
"""

from __future__ import annotations

import pandas as pd

FEATURE_COLUMNS = [
    "foot_traffic",
    "foot_traffic_20",
    "foot_traffic_30",
    "resident_population",
    "worker_population",
    "store_count",
    "opening_rate",
    "closure_rate",
    "estimated_sales",
    "sales_growth_rate",
    "facility_count",
    "transit_score",
]

META_COLUMNS = ["district_code", "business_code", "q_index", "reference_date"]


def build_feature_matrix(labeled_panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """labels.build_labels()의 출력에서 라벨이 있는 행만 골라 학습용 (X, y, meta)를 만든다.

    meta에는 predictions/district_features 레코드로 다시 연결하는 데 필요한
    키(district_code, business_code, q_index, reference_date)만 담는다.
    """
    if "label_available" not in labeled_panel.columns:
        raise ValueError("labels.build_labels()를 먼저 실행해 label_available 컬럼을 만드세요.")

    labeled = labeled_panel[labeled_panel["label_available"]].copy()
    missing = set(FEATURE_COLUMNS) - set(labeled.columns)
    if missing:
        raise ValueError(f"panel에 다음 feature 컬럼이 없습니다: {sorted(missing)}")

    X = labeled[FEATURE_COLUMNS].reset_index(drop=True)
    y = labeled["label_unstable"].astype(int).reset_index(drop=True)
    meta = labeled[META_COLUMNS].reset_index(drop=True)
    return X, y, meta
