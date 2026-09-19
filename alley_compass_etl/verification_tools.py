#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — Verification Tools
PRD §9~§11, §18 (Hallucination Control) 구현

역할
    Recommendation Agent / Risk Agent가 만든 자연어 문장에서
    Verification Agent가 추출한 개별 Claim을, 이 파일의 결정론적
    Pandas/통계 연산으로 원본 데이터와 대조한다.

핵심 원칙 (PRD §10.3)
    AI가 설명하고, AI가 검증 절차를 지휘하며, 숫자는 코드가 증명한다.

여기 있는 6개 Tool은 PRD §11과 1:1 대응한다.
    1. data_lookup            — Data Lookup Tool
    2. percentile             — Percentile Tool
    3. trend                  — Trend Calculator
    4. competition_density    — Competition Density Tool
    5. budget_validator       — Budget Validator
    6. assertion_validator    — Assertion Validator

이 파일은 LLM을 호출하지 않는다. 전부 결정론적 코드다.

데이터 소스
    기본은 로컬 CSV(alley_compass_etl.py --no-upload 로 만든
    data/processed/district_features_debug.csv, 즉 "원본 데이터프레임"
    그 자체)를 읽는다. --supabase 플래그를 주면 실제 Supabase
    district_features 테이블에서 읽는다. 두 경로 모두 같은 컬럼
    구조(district_features 스키마)로 정규화되므로 Tool 함수는
    데이터 소스를 몰라도 된다.

주의: budget_validator
    5종 공식 데이터셋(길단위인구·점포·추정매출·집객시설·직장상주인구)에는
    보증금/임대료 항목이 없다. 따라서 이 Tool은 "추정 보증금이 예산
    이내인가"라는 산술만 검증하며, 추정 보증금 자체는 원본 데이터로
    검증된 값이 아니라는 사실을 결과에 명시적으로 남긴다
    (PRD §20 Unsupported Claim Rate 목표 0% — 검증 불가능한 값을
    검증된 것처럼 보고하지 않기 위함).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from supabase import Client

from alley_compass_etl import get_supabase_client, log, select_all

# ---------------------------------------------------------------------
# 기본 설정
# ---------------------------------------------------------------------

DEFAULT_CSV = Path(__file__).parent / "data" / "processed" / "district_features_debug.csv"

# 이 컬럼들은 업종과 무관하게 "상권 단위"로 결정된다.
# (flat 테이블에는 업종별 행마다 반복 저장되어 있으므로, 업종을
#  지정하지 않고 상권 간 비교를 할 때는 상권 기준으로 중복 제거해야 한다.)
DISTRICT_LEVEL_METRICS = {
    "foot_traffic", "foot_traffic_20", "foot_traffic_30",
    "resident_population", "worker_population",
    "facility_count", "transit_score",
}

# 업종 단위로만 의미가 있는 컬럼 (같은 상권이라도 업종마다 값이 다름)
BUSINESS_LEVEL_METRICS = {
    "store_count", "opening_rate", "closure_rate",
    "estimated_sales", "sales_growth_rate",
}

NUMERIC_COLUMNS = [
    "foot_traffic", "foot_traffic_20", "foot_traffic_30",
    "resident_population", "worker_population",
    "store_count", "opening_rate", "closure_rate",
    "estimated_sales", "sales_growth_rate",
    "competition_density", "facility_count", "transit_score",
]

METRIC_LABELS = {
    "foot_traffic": ("생활인구(유동인구)", "명"),
    "foot_traffic_20": ("20대 유동인구", "명"),
    "foot_traffic_30": ("30대 유동인구", "명"),
    "resident_population": ("상주인구", "명"),
    "worker_population": ("직장인구", "명"),
    "store_count": ("동종업종 점포수", "개"),
    "opening_rate": ("개업률", "%"),
    "closure_rate": ("폐업률", "%"),
    "estimated_sales": ("추정매출", "원"),
    "sales_growth_rate": ("매출 성장률", "%"),
    "facility_count": ("집객시설 수", "개"),
    "transit_score": ("교통 접근성 점수", "점"),
}


class ToolError(Exception):
    """Tool 입력이 원본 데이터와 매칭되지 않을 때. 문장 생성 Agent에게
    그대로 반환해 "검증 불가" 처리를 하도록 한다."""


# ---------------------------------------------------------------------
# 데이터 로딩 — CSV(로컬) 또는 Supabase, 같은 모양으로 정규화
# ---------------------------------------------------------------------

def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_from_csv(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        raise ToolError(
            f"{csv_path} 가 없습니다. 먼저 "
            "'python alley_compass_etl.py ... --no-upload' 로 "
            "district_features_debug.csv 를 만드세요."
        )
    df = pd.read_csv(csv_path)
    df.columns = [c.lstrip("﻿") for c in df.columns]  # BOM 방어
    df["district_code"] = df["district_code"].astype(str)
    df["business_code"] = df["business_code"].astype(str)
    return _coerce_numeric(df)


def _latest_reference_date_value(supabase: Client) -> str | None:
    """district_features 전체에서 가장 최근 reference_date 하나만 가볍게 조회한다."""
    resp = (
        supabase.table("district_features")
        .select("reference_date")
        .order("reference_date", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0]["reference_date"] if resp.data else None


def _merge_masters(supabase: Client, feats: pd.DataFrame) -> pd.DataFrame:
    districts = pd.DataFrame(
        select_all(
            supabase,
            "districts",
            "id,district_code,district_name,gu_name,latitude,longitude,area_m2",
        )
    )
    businesses = pd.DataFrame(
        select_all(supabase, "business_types", "id,business_code,business_name")
    )

    df = feats.merge(
        districts.rename(columns={"id": "district_id"}),
        on="district_id",
        how="left",
    ).merge(
        businesses.rename(columns={"id": "business_type_id"}),
        on="business_type_id",
        how="left",
    )
    df["district_code"] = df["district_code"].astype(str)
    df["business_code"] = df["business_code"].astype(str)
    return _coerce_numeric(df)


def _load_from_supabase(latest_only: bool = False) -> pd.DataFrame:
    """district_features + districts + business_types를 합친 프레임.

    latest_only=True면 가장 최근 분기 한 개만 가져온다 — /rank처럼 "서울
    전체 상권을 한 시점 기준으로 비교"하는 용도는 이걸로 충분하고, 18개
    분기(14만 행 이상) 전체를 메모리에 올릴 필요가 없다. 실제로 Render
    무료 플랜(512MB)에서 전체를 올렸다가 메모리 초과로 죽는 걸 확인했다
    (측정: 시작 128MB → 전체 로드 후 731MB). 과거 분기가 필요한 화면
    (상권 하나의 추이 차트)은 load_district_history()로 그때그때 작게
    따로 받는다.
    """
    load_dotenv()
    supabase = get_supabase_client()

    filters = None
    if latest_only:
        latest_date = _latest_reference_date_value(supabase)
        if latest_date is not None:
            filters = {"reference_date": latest_date}

    feats = pd.DataFrame(select_all(supabase, "district_features", "*", filters=filters))
    if feats.empty:
        raise ToolError(
            "Supabase district_features 테이블이 비어 있습니다. "
            "아직 데이터를 업로드하지 않았다면 --csv 옵션으로 로컬 "
            "district_features_debug.csv 를 쓰세요."
        )
    return _merge_masters(supabase, feats)


def load_feature_frame(
    csv_path: Path | None = None,
    use_supabase: bool = False,
    latest_only: bool = False,
) -> pd.DataFrame:
    """Tool들이 대조할 '원본 데이터프레임'을 반환한다.

    district_features 테이블과 동일한 컬럼 구조
    (district_code, business_code, reference_date, foot_traffic, ...)
    로 정규화되어 있으면 데이터 출처는 CSV든 Supabase든 상관없다.

    latest_only는 Supabase 경로에서만 의미가 있다(CSV는 로컬 개발용이라
    이미 작고, ml/train.py처럼 전체 이력이 필요한 CLI 도구는 이 옵션을
    안 쓴다 — 기본값 False로 기존 동작을 그대로 유지한다).
    """
    if use_supabase:
        return _load_from_supabase(latest_only=latest_only)
    return _load_from_csv(csv_path or DEFAULT_CSV)


def load_district_history(
    district_code: str,
    business_code: str,
    use_supabase: bool = False,
    csv_path: Path | None = None,
) -> pd.DataFrame:
    """한 상권×업종의 전체 분기 이력만 가져온다. /detail의 추이 차트 전용.

    get_frame()의 캐시(latest_only=True)에는 과거 분기가 없으므로, 상세
    화면을 열 때마다 이 함수로 그 상권×업종 하나만 작게(현재 최대 18행)
    따로 조회한다 — 전체 테이블을 메모리에 올리지 않고도 추이를 보여줄
    수 있다.
    """
    dcode, bcode = str(district_code), str(business_code)

    if not use_supabase:
        df = _load_from_csv(csv_path or DEFAULT_CSV)
        return df[(df["district_code"] == dcode) & (df["business_code"] == bcode)]

    supabase = get_supabase_client()
    district = (
        supabase.table("districts")
        .select("id,district_name")
        .eq("district_code", dcode)
        .limit(1)
        .execute()
    )
    business = (
        supabase.table("business_types")
        .select("id,business_name")
        .eq("business_code", bcode)
        .limit(1)
        .execute()
    )
    if not district.data or not business.data:
        return pd.DataFrame()

    rows = (
        supabase.table("district_features")
        .select("*")
        .eq("district_id", district.data[0]["id"])
        .eq("business_type_id", business.data[0]["id"])
        .execute()
    ).data or []
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["district_code"] = dcode
    df["district_name"] = district.data[0]["district_name"]
    df["business_code"] = bcode
    df["business_name"] = business.data[0]["business_name"]
    return _coerce_numeric(df)


def _latest_reference_date(df: pd.DataFrame, mask: pd.Series, metric: str) -> str | None:
    sub = df.loc[mask & df[metric].notna(), "reference_date"]
    if sub.empty:
        return None
    return str(sub.max())


# ---------------------------------------------------------------------
# Tool 1 — Data Lookup Tool
# ---------------------------------------------------------------------

def data_lookup(
    df: pd.DataFrame,
    district_code: str,
    metric: str,
    business_code: str | None = None,
    reference_date: str | None = None,
) -> dict[str, Any]:
    """특정 상권·업종·기간의 원본 값을 조회한다. (PRD §11 Data Lookup Tool)"""
    if metric not in METRIC_LABELS:
        raise ToolError(f"알 수 없는 metric: {metric}")

    mask = df["district_code"] == str(district_code)
    if metric in BUSINESS_LEVEL_METRICS:
        if business_code is None:
            raise ToolError(f"'{metric}'은 업종 단위 지표라 business_code가 필요합니다.")
        mask &= df["business_code"] == str(business_code)

    if reference_date is not None:
        mask &= df["reference_date"] == reference_date
    else:
        as_of = _latest_reference_date(df, mask, metric)
        if as_of is None:
            return {
                "found": False,
                "district_code": district_code,
                "business_code": business_code,
                "metric": metric,
                "reason": "해당 조건에 값이 없습니다 (기준시점 없음)",
            }
        mask &= df["reference_date"] == as_of

    rows = df.loc[mask]
    if rows.empty or rows[metric].isna().all():
        return {
            "found": False,
            "district_code": district_code,
            "business_code": business_code,
            "metric": metric,
            "reason": "해당 조건에 값이 없습니다",
        }

    row = rows.iloc[0]
    label, unit = METRIC_LABELS[metric]
    return {
        "found": True,
        "district_code": district_code,
        "district_name": row.get("district_name"),
        "business_code": business_code,
        "metric": metric,
        "metric_label": label,
        "value": None if pd.isna(row[metric]) else float(row[metric]),
        "unit": unit,
        "as_of": row.get("reference_date"),
    }


# ---------------------------------------------------------------------
# Tool 2 — Percentile Tool
# ---------------------------------------------------------------------

def percentile(
    df: pd.DataFrame,
    metric: str,
    district_code: str,
    business_code: str | None = None,
    reference_date: str | None = None,
) -> dict[str, Any]:
    """서울 전체 후보(같은 업종·같은 기준시점) 중 이 상권의 값이 몇
    퍼센타일인지 계산한다. (PRD §11 Percentile Tool, §10.3 예시)

    rank_pct: 값이 클수록 높은 백분위(0~100).
    top_pct : "상위 N%" 표현용 = 100 - rank_pct.
      값이 클수록 좋은 지표(유동인구 등)는 top_pct를,
      값이 작을수록 좋은 지표(폐업률 등)는 rank_pct를 "상위"로 읽으면 된다.
      어느 쪽으로 읽을지는 호출자(Agent)가 metric의 의미에 맞게 고른다.
    """
    if metric not in METRIC_LABELS:
        raise ToolError(f"알 수 없는 metric: {metric}")
    if metric in BUSINESS_LEVEL_METRICS and business_code is None:
        raise ToolError(f"'{metric}'은 업종 단위 지표라 business_code가 필요합니다.")

    scope = df.copy()
    if business_code is not None:
        scope = scope[scope["business_code"] == str(business_code)]

    as_of = reference_date or _latest_reference_date(
        scope, pd.Series(True, index=scope.index), metric
    )
    if as_of is None:
        raise ToolError(f"'{metric}' 값이 존재하는 기준시점이 없습니다.")
    scope = scope[scope["reference_date"] == as_of]

    if metric in DISTRICT_LEVEL_METRICS:
        scope = scope.drop_duplicates(subset=["district_code"])

    scope = scope.dropna(subset=[metric])
    if str(district_code) not in set(scope["district_code"]):
        raise ToolError(
            f"상권_코드 {district_code} 는 {as_of} 시점 '{metric}' 비교 대상에 없습니다."
        )

    n = len(scope)
    target_value = float(scope.loc[scope["district_code"] == str(district_code), metric].iloc[0])
    rank_pct = float((scope[metric] <= target_value).sum()) / n * 100

    return {
        "district_code": district_code,
        "business_code": business_code,
        "metric": metric,
        "metric_label": METRIC_LABELS[metric][0],
        "as_of": as_of,
        "n_candidates": n,
        "value": target_value,
        "rank_pct": round(rank_pct, 1),
        "top_pct": round(100 - rank_pct, 1),
    }


# ---------------------------------------------------------------------
# Tool 3 — Trend Calculator
# ---------------------------------------------------------------------

def trend(
    df: pd.DataFrame,
    district_code: str,
    metric: str,
    business_code: str | None = None,
    periods: int = 4,
) -> dict[str, Any]:
    """최근 N분기(periods) 간 증가·감소율을 계산한다. (PRD §11 Trend Calculator)

    데이터가 N+1개 분기보다 적으면 추세를 지어내지 않고
    available=False 로 그 사실을 그대로 반환한다.
    """
    if metric not in METRIC_LABELS:
        raise ToolError(f"알 수 없는 metric: {metric}")

    mask = df["district_code"] == str(district_code)
    if metric in BUSINESS_LEVEL_METRICS:
        if business_code is None:
            raise ToolError(f"'{metric}'은 업종 단위 지표라 business_code가 필요합니다.")
        mask &= df["business_code"] == str(business_code)

    series = (
        df.loc[mask, ["reference_date", metric]]
        .dropna()
        .drop_duplicates(subset=["reference_date"])
        .sort_values("reference_date")
    )

    if len(series) < periods + 1:
        return {
            "available": False,
            "district_code": district_code,
            "business_code": business_code,
            "metric": metric,
            "reason": f"{periods}개 분기 전 데이터가 없습니다 (현재 {len(series)}개 분기 보유)",
            "periods_held": len(series),
        }

    base_value = float(series[metric].iloc[-(periods + 1)])
    latest_value = float(series[metric].iloc[-1])
    change_pct = None if base_value == 0 else (latest_value - base_value) / base_value * 100

    return {
        "available": True,
        "district_code": district_code,
        "business_code": business_code,
        "metric": metric,
        "metric_label": METRIC_LABELS[metric][0],
        "base_period": series["reference_date"].iloc[-(periods + 1)],
        "latest_period": series["reference_date"].iloc[-1],
        "base_value": base_value,
        "latest_value": latest_value,
        "change_pct": None if change_pct is None else round(change_pct, 1),
    }


# ---------------------------------------------------------------------
# Tool 4 — Competition Density Tool
# ---------------------------------------------------------------------

DEMAND_COLUMNS = ["foot_traffic", "resident_population", "worker_population"]


def demand_series(frame: pd.DataFrame) -> tuple[pd.Series | None, list[str]]:
    """배후수요 = 유동인구 + 상주인구 + 직장인구.

    일부 컬럼이 없거나 비어 있으면 있는 것만 더하고, 실제로 사용한 컬럼을
    함께 돌려준다 — 어떤 값으로 계산했는지 결과에 남기기 위해서다.
    """
    present = [c for c in DEMAND_COLUMNS if c in frame.columns]
    if not present:
        return None, []
    demand = frame[present].fillna(0).sum(axis=1)
    if not (demand > 0).any():
        return None, []
    return demand, present


def competition_density(
    df: pd.DataFrame,
    district_code: str,
    business_code: str,
    reference_date: str | None = None,
) -> dict[str, Any]:
    """업종별 경쟁강도를 서울 평균과 비교한다. (PRD §11 Competition Density Tool)

    5종 데이터셋에 상권 면적이 없어 '점포수/면적' 밀도는 만들 수 없다. 대신
    면적이 필요 없는 정의를 쓴다.

        점포당 배후수요 = (유동인구 + 상주인구 + 직장인구) / 동종업종 점포수

    값이 클수록 점포 하나가 나눠 갖는 수요가 커서 경쟁이 여유롭다. 없는
    데이터(면적)를 지어내지 않고 가진 데이터만으로 계산하는 방식이며,
    웹 프론트(web/src/lib/scoring.js)도 같은 정의를 쓴다.

    배후수요 컬럼이 전부 비어 있으면 점포수 기준 비교로 물러서고, 어느 기준으로
    판정했는지를 basis 필드에 남긴다.
    """
    scope = df[df["business_code"] == str(business_code)].copy()
    as_of = reference_date or _latest_reference_date(
        scope, pd.Series(True, index=scope.index), "store_count"
    )
    if as_of is None:
        raise ToolError(f"업종 {business_code} 의 store_count 데이터가 없습니다.")
    scope = scope[scope["reference_date"] == as_of].dropna(subset=["store_count"])
    scope = scope[scope["store_count"] > 0]

    if str(district_code) not in set(scope["district_code"]):
        raise ToolError(f"상권_코드 {district_code} 는 {as_of} 시점 비교 대상에 없습니다.")

    is_target = scope["district_code"] == str(district_code)
    district_stores = float(scope.loc[is_target, "store_count"].iloc[0])
    citywide_avg_stores = float(scope["store_count"].mean())

    result: dict[str, Any] = {
        "district_code": district_code,
        "business_code": business_code,
        "as_of": as_of,
        "district_store_count": district_stores,
        "citywide_avg_store_count": round(citywide_avg_stores, 2),
        "citywide_median_store_count": float(scope["store_count"].median()),
        "store_count_ratio_to_avg": (
            None if citywide_avg_stores == 0 else round(district_stores / citywide_avg_stores, 2)
        ),
        "n_districts": len(scope),
    }

    demand, used_columns = demand_series(scope)

    if demand is None:
        result.update({
            "basis": "store_count",
            "demand_columns_used": [],
            "district_demand": None,
            "district_demand_per_store": None,
            "citywide_avg_demand_per_store": None,
            # 이 경우에만 점포수 비율이 판정 기준이 된다 (값이 클수록 과밀)
            "ratio_to_avg": result["store_count_ratio_to_avg"],
            "note": (
                "배후수요(유동·상주·직장) 컬럼이 비어 있어 점포수 기준으로만 "
                "비교했습니다. 값이 클수록 점포가 많다는 뜻이며, 수요 대비 "
                "경쟁강도는 아닙니다."
            ),
        })
        return result

    per_store = demand / scope["store_count"]
    district_per_store = float(per_store.loc[is_target].iloc[0])
    citywide_avg_per_store = float(per_store.mean())

    result.update({
        "basis": "demand_per_store",
        "demand_columns_used": used_columns,
        "district_demand": round(float(demand.loc[is_target].iloc[0]), 2),
        "district_demand_per_store": round(district_per_store, 2),
        "citywide_avg_demand_per_store": round(citywide_avg_per_store, 2),
        # 값이 클수록 점포당 수요가 커서 경쟁이 여유롭다 (점포수 비율과 방향이 반대다)
        "ratio_to_avg": (
            None
            if citywide_avg_per_store == 0
            else round(district_per_store / citywide_avg_per_store, 2)
        ),
        "note": (
            "상권 면적 미보유 — '점포수/면적' 밀도 대신 수요 대비 공급"
            f"(배후수요/점포수, 사용 컬럼 {'+'.join(used_columns)})으로 "
            "계산했습니다. 값이 클수록 경쟁이 여유롭습니다."
        ),
    })
    return result


# ---------------------------------------------------------------------
# Tool 5 — Budget Validator
# ---------------------------------------------------------------------

def budget_validator(
    estimated_deposit: float,
    budget: float,
) -> dict[str, Any]:
    """예상 보증금이 사용자 예산 이내인지 검증한다. (PRD §11 Budget Validator)

    주의: 5종 공식 데이터셋에는 보증금/임대료 항목이 없다. 따라서
    estimated_deposit은 원본 데이터로 검증된 값이 아니라 외부 추정치이며,
    이 함수는 "그 추정치와 예산의 산술 비교"만 검증한다. verified_by_data
    를 False로 두어 이 사실을 결과에 남긴다.
    """
    fits = estimated_deposit <= budget
    margin = budget - estimated_deposit
    margin_pct = None if budget == 0 else round(margin / budget * 100, 1)

    return {
        "estimated_deposit": estimated_deposit,
        "budget": budget,
        "fits_budget": bool(fits),
        "margin": margin,
        "margin_pct": margin_pct,
        "verified_by_data": False,
        "note": (
            "estimated_deposit은 5종 공식 데이터셋에 없는 외부 추정치입니다. "
            "산술 비교만 검증되었고, 추정치 자체의 사실 여부는 검증되지 않았습니다."
        ),
    }


# ---------------------------------------------------------------------
# Tool 6 — Assertion Validator
# ---------------------------------------------------------------------

def _round_to_band(value: float, band: float = 5.0) -> float:
    """'상위 8%' 같은 과도하게 정밀한 표현 대신 '상위 약 10%'로
    완화할 때 쓰는 반올림. PRD §10.3의 최종 표현과 같은 방식이다."""
    return round(value / band) * band


def assertion_validator(
    *,
    claimed_value: float | None = None,
    actual_value: float | None = None,
    tolerance: float = 1.5,
    claimed_text: str | None = None,
    actual_text: str | None = None,
) -> dict[str, Any]:
    """생성 문장에 포함된 수치/문구가 계산 결과와 허용오차 내에서
    일치하는지 판단한다. (PRD §11 Assertion Validator, §10.3 최종 원칙)

    숫자 비교: |claimed - actual| <= tolerance 면 PASS.
    문구 비교(claimed_text/actual_text): 완전 일치해야 PASS.
    """
    if claimed_value is not None and actual_value is not None:
        diff = abs(claimed_value - actual_value)
        verified = diff <= tolerance
        suggestion = (
            None if verified
            else f"상위 약 {_round_to_band(100 - actual_value)}%"
            if 0 <= actual_value <= 100
            else round(actual_value, 1)
        )
        return {
            "verified": verified,
            "claimed_value": claimed_value,
            "actual_value": actual_value,
            "diff": round(diff, 2),
            "tolerance": tolerance,
            "verification_tool": "Assertion Validator",
            "verification_reason": (
                "허용오차 이내로 일치"
                if verified
                else f"허용오차({tolerance}) 초과 — 차이 {round(diff, 2)}"
            ),
            "suggested_correction": suggestion,
        }

    if claimed_text is not None and actual_text is not None:
        verified = claimed_text.strip() == actual_text.strip()
        return {
            "verified": verified,
            "claimed_text": claimed_text,
            "actual_text": actual_text,
            "verification_tool": "Assertion Validator",
            "verification_reason": "문구 일치" if verified else "문구 불일치",
        }

    raise ToolError("claimed_value/actual_value 또는 claimed_text/actual_text 쌍을 넘겨야 합니다.")


# ---------------------------------------------------------------------
# 통합 디스패처 — Verification Agent가 claim 하나를 넘기면 알맞은
# Tool을 골라 실행하고, verification_claims 테이블 그대로 반환한다.
# ---------------------------------------------------------------------

VerificationType = Literal["raw_value", "percentile", "trend", "competition", "budget"]


@dataclass
class Claim:
    claim_text: str
    metric: str | None
    verification_type: VerificationType
    district_code: str | None = None
    business_code: str | None = None
    claimed_value: float | None = None
    tolerance: float = 1.5
    extra: dict[str, Any] = field(default_factory=dict)


def verify_claim(df: pd.DataFrame, claim: Claim) -> dict[str, Any]:
    """Claim 하나를 검증하고 verification_claims 테이블 스키마와
    동일한 필드를 가진 dict를 반환한다."""
    try:
        if claim.verification_type == "raw_value":
            looked_up = data_lookup(
                df, claim.district_code, claim.metric, claim.business_code
            )
            actual = looked_up.get("value")
            tool_name = "Data Lookup Tool"

        elif claim.verification_type == "percentile":
            p = percentile(df, claim.metric, claim.district_code, claim.business_code)
            actual = p["top_pct"] if claim.extra.get("direction") != "rank" else p["rank_pct"]
            tool_name = "Percentile Tool"

        elif claim.verification_type == "trend":
            t = trend(
                df, claim.district_code, claim.metric, claim.business_code,
                periods=claim.extra.get("periods", 4),
            )
            if not t["available"]:
                return {
                    "claim_text": claim.claim_text,
                    "metric": claim.metric,
                    "verification_type": claim.verification_type,
                    "claimed_value": claim.claimed_value,
                    "actual_value": None,
                    "verified": False,
                    "verification_tool": "Trend Calculator",
                    "verification_reason": t["reason"],
                }
            actual = t["change_pct"]
            tool_name = "Trend Calculator"

        elif claim.verification_type == "competition":
            c = competition_density(df, claim.district_code, claim.business_code)
            actual = c["ratio_to_avg"]
            tool_name = "Competition Density Tool"

        elif claim.verification_type == "budget":
            # claim.claimed_value      = 문장이 언급한 추정 보증금(만원)
            # claim.extra["budget"]    = 사용자가 입력한 예산(만원)
            # claim.extra["claimed_fits"] = 문장의 결론(예산 이내라고 주장했는가)
            b = budget_validator(claim.claimed_value, claim.extra["budget"])
            claimed_fits = claim.extra["claimed_fits"]
            arithmetic_ok = b["fits_budget"] == claimed_fits
            return {
                "claim_text": claim.claim_text,
                "metric": "budget_fit",
                "verification_type": "budget",
                "claimed_value": claim.claimed_value,
                "actual_value": b["margin"],
                "verified": arithmetic_ok,
                "verification_tool": "Budget Validator",
                "verification_reason": (
                    f"산술 결론 일치 (fits={b['fits_budget']})"
                    if arithmetic_ok
                    else f"산술 결론 불일치 — 실제 fits={b['fits_budget']}, 문장 주장={claimed_fits}"
                ) + f" · {b['note']}",
            }

        else:
            raise ToolError(f"알 수 없는 verification_type: {claim.verification_type}")

    except ToolError as exc:
        return {
            "claim_text": claim.claim_text,
            "metric": claim.metric,
            "verification_type": claim.verification_type,
            "claimed_value": claim.claimed_value,
            "actual_value": None,
            "verified": False,
            "verification_tool": None,
            "verification_reason": f"검증 불가: {exc}",
        }

    if actual is None:
        # 예: raw_value 조회 대상이 원본 데이터에 없는 경우.
        # assertion_validator는 claimed/actual 중 하나가 없으면 ToolError를
        # 던지므로, 여기서 먼저 "검증 불가"로 명확히 반환한다.
        return {
            "claim_text": claim.claim_text,
            "metric": claim.metric,
            "verification_type": claim.verification_type,
            "claimed_value": claim.claimed_value,
            "actual_value": None,
            "verified": False,
            "verification_tool": tool_name,
            "verification_reason": "원본 데이터에 해당 값이 없어 검증할 수 없습니다.",
        }

    result = assertion_validator(
        claimed_value=claim.claimed_value,
        actual_value=actual,
        tolerance=claim.tolerance,
    )
    return {
        "claim_text": claim.claim_text,
        "metric": claim.metric,
        "verification_type": claim.verification_type,
        "claimed_value": claim.claimed_value,
        "actual_value": actual,
        "tolerance": claim.tolerance,
        "verified": result["verified"],
        "verification_tool": tool_name,
        "verification_reason": result["verification_reason"],
    }


def to_verification_claim_row(analysis_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """verify_claim()의 결과를 verification_claims 테이블 insert용 행으로 변환한다.
    (에이전트 체인이 준비되어 agent_analyses.id가 생기면 그때 사용)"""
    return {
        "analysis_id": analysis_id,
        "claim_text": result["claim_text"],
        "metric": result.get("metric"),
        "verification_type": result.get("verification_type"),
        "claimed_value": result.get("claimed_value"),
        "actual_value": result.get("actual_value"),
        "tolerance": result.get("tolerance"),
        "verified": result["verified"],
        "verification_tool": result.get("verification_tool"),
        "verification_reason": result.get("verification_reason"),
    }


# ---------------------------------------------------------------------
# 데모용 상권/업종 선택 — verification_tools/fact_sheet/pipeline 데모가 공유
# ---------------------------------------------------------------------

def pick_demo_target(
    df: pd.DataFrame,
    business_code: str | None = None,
    district_code: str | None = None,
) -> tuple[str, str, pd.DataFrame]:
    """데모/CLI에서 쓸 (business_code, district_code, 해당 업종 행)을 고른다.

    district_code를 안 주면 PRD §10.3 예시("상위 8%")와 비슷한 그림이
    나오도록 20대 유동인구 상위 8% 부근 상권을 고른다 (1위를 고르면
    top_pct=0%가 되어 오차 예시를 만들기 애매해진다).
    """
    if business_code is None:
        business_code = df["business_code"].dropna().iloc[0]
    biz_rows = df[df["business_code"] == business_code]

    if district_code is None:
        ranked = (
            biz_rows.dropna(subset=["foot_traffic_20"])
            .sort_values("foot_traffic_20", ascending=False)["district_code"]
            .reset_index(drop=True)
        )
        district_code = ranked.iloc[max(0, int(len(ranked) * 0.08))]

    return str(business_code), str(district_code), biz_rows


# ---------------------------------------------------------------------
# 데모 — 실제 데이터로 PRD §10.3 흐름을 그대로 재현
# ---------------------------------------------------------------------

def _demo(df: pd.DataFrame, business_code: str | None, district_code: str | None) -> None:
    business_code, district_code, biz_rows = pick_demo_target(df, business_code, district_code)

    match = biz_rows[biz_rows["district_code"] == str(district_code)]
    if match.empty:
        raise ToolError(
            f"상권_코드 {district_code} 는 업종 {business_code} 데이터에 없습니다."
        )
    district_name = match["district_name"].iloc[0]
    biz_name = biz_rows["business_name"].iloc[0]

    log(f"=== 데모 대상: {district_name}({district_code}) × {biz_name}({business_code}) ===")

    p = percentile(df, "foot_traffic_20", district_code, business_code)
    log(f"[Percentile Tool] 20대 유동인구 실제 상위 {p['top_pct']}% "
        f"(값={p['value']:,.0f}명, 후보 {p['n_candidates']}곳, 기준={p['as_of']})")

    # PRD §10.3 재현: Recommendation Agent가 살짝 틀리게 반올림했다고 가정
    wrong_claim = min(max(round(p["top_pct"]) + 3, 0), 100)  # 일부러 어긋나게 (+3%p)
    log(f"\n[1차 생성 문장] \"{district_name}의 20대 유동인구는 "
        f"서울 골목상권 상위 {wrong_claim}%입니다.\"")
    check1 = assertion_validator(claimed_value=wrong_claim, actual_value=p["top_pct"], tolerance=1.5)
    status1 = "PASS" if check1["verified"] else "FAIL → 반려"
    log(f"[Assertion Validator] {status1} — {check1['verification_reason']}")
    if not check1["verified"]:
        log(f"[재생성] \"...서울 골목상권 상위 {_round_to_band(p['top_pct'])}% 수준입니다.\"")

    log("")
    lookup = data_lookup(df, district_code, "estimated_sales", business_code)
    if lookup["found"]:
        log(f"[Data Lookup Tool] 추정매출 = {lookup['value']:,.0f}{lookup['unit']} "
            f"(기준={lookup['as_of']})")

    log("")
    comp = competition_density(df, district_code, business_code)
    if comp["basis"] == "demand_per_store":
        log(f"[Competition Density Tool] 점포 {comp['district_store_count']:.0f}개 · "
            f"점포당 배후수요={comp['district_demand_per_store']:,.1f} "
            f"(서울 평균 {comp['citywide_avg_demand_per_store']:,.1f}, "
            f"비율 {comp['ratio_to_avg']}) — {comp['note']}")
    else:
        log(f"[Competition Density Tool] 이 상권 점포수={comp['district_store_count']:.0f}개, "
            f"서울 평균={comp['citywide_avg_store_count']:.1f}개 "
            f"(비율 {comp['ratio_to_avg']}) — {comp['note']}")

    log("")
    tr = trend(df, district_code, "estimated_sales", business_code, periods=4)
    if tr["available"]:
        log(f"[Trend Calculator] 최근 4분기 매출 변화 {tr['change_pct']}% "
            f"({tr['base_period']} → {tr['latest_period']})")
    else:
        log(f"[Trend Calculator] 계산 불가 — {tr['reason']} "
            "(여러 분기 업로드 후 다시 실행하면 채워집니다)")

    log("")
    budget_claim = 5000
    est_deposit = 6200
    bv = budget_validator(est_deposit, budget_claim)
    log(f"[Budget Validator] 추정 보증금 {est_deposit:,}만원 vs 예산 {budget_claim:,}만원 "
        f"→ fits={bv['fits_budget']} ({bv['note']})")

    log("\n=== verify_claim() 배치 실행 (Verification Agent가 실제로 쓰는 형태) ===")
    claims = [
        Claim(
            claim_text=f"{district_name}의 20대 유동인구는 상위 {p['top_pct']}%입니다.",
            metric="foot_traffic_20",
            verification_type="percentile",
            district_code=district_code,
            business_code=business_code,
            claimed_value=p["top_pct"],
        ),
        Claim(
            claim_text=f"{district_name}의 20대 유동인구는 상위 {wrong_claim}%입니다.",
            metric="foot_traffic_20",
            verification_type="percentile",
            district_code=district_code,
            business_code=business_code,
            claimed_value=wrong_claim,
        ),
        Claim(
            claim_text=f"예상 보증금 {est_deposit:,}만원은 예산 {budget_claim:,}만원 이내입니다.",
            metric="budget_fit",
            verification_type="budget",
            claimed_value=est_deposit,
            extra={"budget": budget_claim, "claimed_fits": True},
        ),
        Claim(
            claim_text=f"예상 보증금 {est_deposit:,}만원은 예산 {budget_claim:,}만원을 초과합니다.",
            metric="budget_fit",
            verification_type="budget",
            claimed_value=est_deposit,
            extra={"budget": budget_claim, "claimed_fits": False},
        ),
    ]
    for claim in claims:
        r = verify_claim(df, claim)
        mark = "✅ PASS" if r["verified"] else "❌ FAIL"
        log(f"{mark} [{r['verification_tool']}] \"{r['claim_text']}\" — {r['verification_reason']}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="골목 컴퍼스 Verification Tools 데모/CLI")
    p.add_argument("--csv", type=Path, default=None, help="district_features_debug.csv 경로")
    p.add_argument("--supabase", action="store_true", help="로컬 CSV 대신 Supabase에서 로드")
    p.add_argument("--business-code", default=None, help="데모에 쓸 업종 코드")
    p.add_argument("--district-code", default=None, help="데모에 쓸 상권_코드")
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    df = load_feature_frame(csv_path=args.csv, use_supabase=args.supabase)
    log(f"로드 완료: {len(df):,}행, 상권 {df['district_code'].nunique():,}개, "
        f"업종 {df['business_code'].nunique()}개")
    _demo(df, args.business_code, args.district_code)


if __name__ == "__main__":
    try:
        main()
    except ToolError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)
