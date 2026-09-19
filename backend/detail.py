#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — 상권 상세 지표
PRD §17.4 상세 근거 Drawer가 필요로 하는 데이터를 한 번에 만들어 준다.

    district_features
        -> 상권 진단 4영역 (잠재고객 / 경쟁강도 / 영업환경 / 비용)
        -> 시계열 3종 (시간대별 유동인구 / 분기별 매출 / 분기별 폐업률)
        -> 경쟁강도 비교 (이 상권 vs 서울 평균)

왜 프론트가 아니라 여기서 계산하는가
    백분위 정의가 verification_tools.percentile() 하나여야 하기 때문이다.
    화면이 "상위 12%"라고 말하는데 검증 Tool이 다르게 판정하면, 같은 숫자를
    두고 서로 다른 말을 하게 된다. 그래서 집계는 전부 이 파일에서 하고
    프론트는 받은 값을 그리기만 한다.

없는 데이터에 대한 규칙
    분기가 1개뿐이라 추세를 못 만들거나, 임차료처럼 데이터셋에 아예 없는
    항목은 값을 지어내지 않는다. available=False 와 이유(reason)를 돌려주고
    화면이 "데이터 미보유"로 표시한다 (PRD §20 Unsupported Claim Rate 0%).
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from verification_tools import (
    METRIC_LABELS,
    ToolError,
    demand_series,
    percentile,
)

# 시간대별 유동인구 — ETL이 extra_features로 보내는 6개 구간
HOUR_BUCKETS: list[tuple[str, str]] = [
    ("foot_00_06", "00–06시"),
    ("foot_06_11", "06–11시"),
    ("foot_11_14", "11–14시"),
    ("foot_14_17", "14–17시"),
    ("foot_17_21", "17–21시"),
    ("foot_21_24", "21–24시"),
]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def _cell(row: pd.Series, column: str) -> float | None:
    """평면 컬럼(로컬 CSV)과 extra_features JSONB(Supabase) 양쪽에서 값을 꺼낸다.

    같은 파이프라인이 만든 데이터인데 저장 형태만 다르다 — 로컬 디버그 CSV는
    전처리 직후의 평면 프레임이고, Supabase는 고정 컬럼 외를 extra_features에
    모아 둔다. 호출부가 이 차이를 알 필요는 없다.
    """
    if column in row.index and not _is_missing(row[column]):
        return float(row[column])

    extra = row.get("extra_features")
    if isinstance(extra, dict) and not _is_missing(extra.get(column)):
        return float(extra[column])

    return None


def _safe_percentile(
    df: pd.DataFrame, metric: str, district_code: str, business_code: str
) -> dict[str, Any] | None:
    """비교 대상이 없으면 None. 백분위를 지어내지 않는다."""
    try:
        return percentile(df, metric, district_code, business_code)
    except ToolError:
        return None


def _row(
    label: str, value: Any, top: float | None = None, unverified: bool = False
) -> dict[str, Any]:
    return {"label": label, "value": value, "top": top, "unverified": unverified}


def _fmt(value: float | None, unit: str = "", digits: int = 0) -> str:
    if value is None:
        return "미보유"
    return f"{value:,.{digits}f}{unit}"


def _area_score(contributions: list[float]) -> int | None:
    """영역 지수 = 구성 지표 백분위의 평균. 하나도 없으면 None."""
    if not contributions:
        return None
    return round(sum(contributions) / len(contributions))


# ---------------------------------------------------------------------
# 진단 영역 1 — 잠재고객
# ---------------------------------------------------------------------

_CUSTOMER_METRICS = [
    ("foot_traffic_20", "20대 유동인구", "명"),
    ("foot_traffic_30", "30대 유동인구", "명"),
    ("resident_population", "상주인구", "명"),
    ("worker_population", "직장인구", "명"),
]


def _customers_area(df: pd.DataFrame, dcode: str, bcode: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    ranks: list[float] = []

    for metric, label, unit in _CUSTOMER_METRICS:
        p = _safe_percentile(df, metric, dcode, bcode)
        if p is None:
            rows.append(_row(label, "미보유"))
            continue
        rows.append(_row(label, _fmt(p["value"], unit), p["top_pct"]))
        ranks.append(p["rank_pct"])

    score = _area_score(ranks)

    return {
        "key": "customers",
        "label": "잠재고객",
        "caption": "유동 · 상주 · 직장 배후 수요",
        "available": score is not None,
        "score": score,
        "top_pct": None if score is None else round(100 - score, 1),
        "rows": rows,
        "note": None
        if score is not None
        else "유동·상주·직장인구 컬럼이 모두 비어 있어 지수를 계산하지 않았습니다.",
    }


# ---------------------------------------------------------------------
# 진단 영역 2 — 경쟁강도 (수요 대비 공급)
# ---------------------------------------------------------------------

def _competition_frame(df: pd.DataFrame, bcode: str, as_of: str) -> pd.DataFrame | None:
    """같은 업종·같은 기준시점에서 점포당 배후수요를 계산한 비교 모집단.

    면적 데이터가 없어 '점포수/면적' 밀도를 만들 수 없으므로 수요 대비 공급으로 본다.
    정의는 ETL의 extra_features.demand_per_store,
    verification_tools.competition_density() 와 동일하다.
    """
    scope = df[(df["business_code"] == str(bcode)) & (df["reference_date"] == as_of)]
    scope = scope.drop_duplicates(subset=["district_code"])
    scope = scope[scope["store_count"] > 0]
    if scope.empty:
        return None

    demand, _ = demand_series(scope)
    if demand is None:
        return None

    out = scope[["district_code", "district_name", "store_count"]].copy()
    out["demand_per_store"] = demand / scope["store_count"]
    return out.dropna(subset=["demand_per_store"])


def _competition_area(
    df: pd.DataFrame, dcode: str, bcode: str, as_of: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(진단 영역, 차트용 비교값) 을 함께 돌려준다 — 같은 계산을 두 번 하지 않으려고."""
    frame = _competition_frame(df, bcode, as_of)
    unavailable = {
        "key": "competition",
        "label": "경쟁강도",
        "caption": "수요 대비 공급 — 점포 1개가 나눠 갖는 배후수요",
        "available": False,
        "score": None,
        "top_pct": None,
        "rows": [_row("동종업종 점포수", "미보유"), _row("점포당 배후수요", "미보유")],
        "note": "배후수요(유동·상주·직장) 또는 점포수 데이터가 없어 경쟁강도를 계산하지 않았습니다.",
    }

    if frame is None or str(dcode) not in set(frame["district_code"]):
        return unavailable, None

    mine = frame[frame["district_code"] == str(dcode)].iloc[0]
    value = float(mine["demand_per_store"])
    stores = int(mine["store_count"])
    city_avg = float(frame["demand_per_store"].mean())
    n = len(frame)
    rank_pct = float((frame["demand_per_store"] <= value).sum()) / n * 100
    ratio = value / city_avg if city_avg else None

    area = {
        "key": "competition",
        "label": "경쟁강도",
        "caption": "수요 대비 공급 — 점포 1개가 나눠 갖는 배후수요",
        "available": True,
        "score": round(rank_pct),
        "top_pct": round(100 - rank_pct, 1),
        "rows": [
            _row("동종업종 점포수", f"{stores:,}개"),
            _row("점포당 배후수요", f"{value:,.1f}", round(100 - rank_pct, 1)),
            _row(
                "서울 골목 평균 대비",
                "—" if ratio is None else f"평균의 {ratio * 100:.0f}%",
            ),
        ],
        "note": (
            "상권 면적 데이터가 없어 '점포수/면적' 밀도 대신 수요 대비 공급으로 계산했습니다. "
            "값이 클수록 점포 하나가 나눠 갖는 수요가 커서 경쟁이 여유롭다는 뜻입니다."
        ),
    }

    chart = {
        "district_value": round(value, 1),
        "city_avg": round(city_avg, 1),
        "store_count": stores,
        "n_districts": n,
        "ratio_to_avg": None if ratio is None else round(ratio, 2),
    }
    return area, chart


# ---------------------------------------------------------------------
# 진단 영역 3 — 영업환경
# ---------------------------------------------------------------------

def _environment_area(df: pd.DataFrame, dcode: str, bcode: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    ranks: list[float] = []

    sales = _safe_percentile(df, "estimated_sales", dcode, bcode)
    if sales is None:
        rows.append(_row("추정매출 수준", "미보유"))
    else:
        # 원 단위로 들어오므로 만원으로 바꿔 보여준다
        rows.append(_row("추정매출 수준", f"{sales['value'] / 10000:,.0f}만원/월", sales["top_pct"]))
        ranks.append(sales["rank_pct"])

    growth = _safe_percentile(df, "sales_growth_rate", dcode, bcode)
    if growth is None:
        rows.append(_row("매출 성장률 (전분기 대비)", "분기 부족"))
    else:
        rows.append(_row("매출 성장률 (전분기 대비)", f"{growth['value'] * 100:+.1f}%", growth["top_pct"]))
        ranks.append(growth["rank_pct"])

    closure = _safe_percentile(df, "closure_rate", dcode, bcode)
    if closure is None:
        rows.append(_row("폐업률", "미보유"))
    else:
        # 폐업률은 낮을수록 좋다. 행의 "상위 N%"는 다른 지표와 같은 뜻 —
        # "서울에서 이 값이 큰 축으로 N%"이다. 그래서 top_pct 를 그대로 쓰되,
        # 라벨에 방향을 적어 '상위'가 좋은 뜻으로 읽히지 않게 한다.
        # 영역 지수에는 뒤집어 넣는다(폐업률이 높을수록 영업환경 점수가 낮아야 하므로).
        rows.append(_row("폐업률 (높을수록 나쁨)", f"{closure['value']:.1f}%", closure["top_pct"]))
        ranks.append(100 - closure["rank_pct"])

    transit = _safe_percentile(df, "transit_score", dcode, bcode)
    if transit is None:
        rows.append(_row("교통 접근성", "미보유"))
    else:
        rows.append(_row("교통 접근성", f"지수 {transit['value']:.0f}", transit["top_pct"]))
        ranks.append(transit["rank_pct"])

    facility = _safe_percentile(df, "facility_count", dcode, bcode)
    if facility is None:
        rows.append(_row("집객시설 수", "미보유"))
    else:
        rows.append(_row("집객시설 수", f"{facility['value']:,.0f}개", facility["top_pct"]))
        ranks.append(facility["rank_pct"])

    score = _area_score(ranks)

    return {
        "key": "environment",
        "label": "영업환경",
        "caption": "매출 · 폐업률 · 집객/교통 접근성",
        "available": score is not None,
        "score": score,
        "top_pct": None if score is None else round(100 - score, 1),
        "rows": rows,
        "note": None,
    }


# ---------------------------------------------------------------------
# 진단 영역 4 — 비용 (구조적으로 데이터가 없다)
# ---------------------------------------------------------------------

def _cost_area() -> dict[str, Any]:
    return {
        "key": "cost",
        "label": "비용",
        "caption": "보증금 · 임차료 · 공실률",
        "available": False,
        "score": None,
        "top_pct": None,
        "rows": [
            _row("보증금", "미보유"),
            _row("월 임차료", "미보유"),
            _row("공실률", "미보유"),
        ],
        "note": (
            "서울시 상권분석 5종 데이터셋에는 임차료·공실률·보증금이 없습니다. 그래서 이 영역은 "
            "점수를 만들지 않습니다. 실측을 붙이려면 부동산원 상업용 부동산 임대조사를 "
            "상권_코드에 매핑하는 단계가 선행되어야 합니다."
        ),
    }


# ---------------------------------------------------------------------
# 시계열
# ---------------------------------------------------------------------

def _hourly_series(row: pd.Series) -> dict[str, Any]:
    """시간대별 유동인구 6구간. 하나라도 있으면 있는 것만 돌려준다."""
    points = []
    for column, label in HOUR_BUCKETS:
        value = _cell(row, column)
        if value is not None:
            points.append({"label": label, "value": round(value)})

    if not points:
        return {
            "available": False,
            "reason": "시간대별 유동인구(foot_00_06 …)가 수집되지 않았습니다.",
            "points": [],
        }
    return {"available": True, "reason": None, "points": points}


def _quarterly_series(
    history: pd.DataFrame, metric: str, scale: float = 1.0, digits: int = 0
) -> dict[str, Any]:
    """한 상권×업종의 분기별 시계열.

    분기가 1개뿐이면 그래프를 그릴 수는 있어도 추세라고 부를 수 없다.
    points 는 그대로 주되 available 로 그 사실을 알린다 — 없는 추세를
    직선으로 이어 붙여 보여주지 않기 위해서다.
    """
    frame = history.dropna(subset=[metric]).sort_values("reference_date")
    points = [
        {"label": str(r.reference_date), "value": round(float(getattr(r, metric)) * scale, digits)}
        for r in frame.itertuples()
    ]

    if len(points) < 2:
        return {
            "available": False,
            "reason": f"분기가 {len(points)}개뿐이라 추세를 계산할 수 없습니다. "
            "alley_compass_etl.py로 여러 분기를 수집하세요.",
            "points": points,
        }
    return {"available": True, "reason": None, "points": points}


# ---------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------

def build_detail(
    df: pd.DataFrame, history: pd.DataFrame, district_code: str, business_code: str
) -> dict[str, Any]:
    """상세 Drawer 한 번 열 때 필요한 모든 값을 한 응답으로 만든다.

    Claude를 호출하지 않는다 — 전부 결정론적 집계다. 그래서 상권을 열 때마다
    불러도 과금되지 않는다(근거 문장 생성만 /agents 로 분리돼 있다).

    df와 history는 용도가 다르다.
      df      서울 전체 상권의 "최신 분기 한 개"(get_frame() 캐시) —
              잠재고객·경쟁강도·영업환경처럼 다른 상권과 비교하는 데 쓴다.
      history 이 상권×업종 하나의 전체 분기 이력(verification_tools.
              load_district_history()로 그때그때 따로 조회) — 추이
              차트·수집 분기 수 계산에만 쓴다.
    이렇게 나눈 이유: df를 매번 전체 이력으로 캐싱하면 14만 행 이상을
    메모리에 올려야 해서(Render 무료 플랜에서 실제로 메모리 초과가 났다),
    "여러 상권 비교"에는 최신 분기 하나면 충분하다는 점을 이용해 쪼갰다.
    """
    dcode, bcode = str(district_code), str(business_code)

    if history.empty:
        raise ToolError(f"상권_코드 {dcode} × 업종 {bcode} 데이터가 없습니다.")

    as_of = str(df["reference_date"].max()) if not df.empty else str(history["reference_date"].max())
    row = history[history["reference_date"] == history["reference_date"].max()].iloc[0]

    competition_area, competition_chart = _competition_area(df, dcode, bcode, as_of)

    return {
        "district_code": dcode,
        "district_name": str(row.get("district_name") or dcode),
        "business_code": bcode,
        "business_name": str(row.get("business_name") or bcode),
        "as_of": as_of,
        "quarters_collected": int(history["reference_date"].nunique()),
        "diagnostics": [
            _customers_area(df, dcode, bcode),
            competition_area,
            _environment_area(df, dcode, bcode),
            _cost_area(),
        ],
        "series": {
            "hourly": _hourly_series(row),
            "sales": _quarterly_series(history, "estimated_sales", scale=1 / 10000),
            "closure": _quarterly_series(history, "closure_rate", digits=1),
            "competition": competition_chart,
        },
        "metrics_available": sorted(
            m for m in METRIC_LABELS if m in history.columns and history[m].notna().any()
        ),
    }
