#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass)
서울시 상권분석 Open API -> 전처리 -> Supabase 자동 적재

대상 데이터
1) 길단위인구-상권     VwsmTrdarFlpopQq
2) 점포-상권           VwsmTrdarStorQq
3) 추정매출-상권       VwsmTrdarSelngQq
4) 집객시설-상권       VwsmTrdarFcltyQq
5-1) 직장인구-상권     VwsmTrdarWrcPopltnQq
5-2) 상주인구-상권     VwsmTrdarRepopQq

주의
- 5번은 PRD에서 하나의 "배후 인구" 항목으로 묶었지만 실제 Open API는 2개 서비스입니다.
- 이 스크립트는 2021년 이후 데이터만 사용하도록 제한합니다.
- districts.gu_name / latitude / longitude는 위 데이터만으로 확보할 수 없어 NULL로 둡니다.
  지도 좌표는 이후 "영역-상권" 데이터나 별도 geocoding 단계에서 채우세요.
- competition_density는 상권 면적 데이터가 없으므로 임의 계산하지 않고 NULL로 둡니다.
  현재는 store_count를 경쟁강도 feature로 사용합니다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from supabase import Client, create_client


# ---------------------------------------------------------------------
# 기본 설정
# ---------------------------------------------------------------------

SEOUL_API_BASE = "http://openapi.seoul.go.kr:8088"
PAGE_SIZE = 1000
MIN_SUPPORTED_QUARTER = 20211

SERVICES = {
    "foot": {
        "service": "VwsmTrdarFlpopQq",
        "quarter_filter": True,
    },
    "stores": {
        "service": "VwsmTrdarStorQq",
        "quarter_filter": True,
    },
    "sales": {
        "service": "VwsmTrdarSelngQq",
        "quarter_filter": True,
    },
    "facilities": {
        "service": "VwsmTrdarFcltyQq",
        "quarter_filter": False,
    },
    "worker": {
        "service": "VwsmTrdarWrcPopltnQq",
        "quarter_filter": False,
    },
    "resident": {
        "service": "VwsmTrdarRepopQq",
        "quarter_filter": False,
    },
}


# ---------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def quarter_to_tuple(q: int | str) -> tuple[int, int]:
    q = str(q)
    if len(q) != 5 or not q.isdigit():
        raise ValueError(f"분기 코드는 YYYYQ 형식이어야 합니다. 예: 20241 / 입력={q}")
    year = int(q[:4])
    quarter = int(q[4])
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"분기는 1~4여야 합니다: {q}")
    return year, quarter


def quarter_index(q: int | str) -> int:
    year, quarter = quarter_to_tuple(q)
    return year * 4 + quarter


def quarter_start_date(q: int | str) -> str:
    year, quarter = quarter_to_tuple(q)
    month = {1: 1, 2: 4, 3: 7, 4: 10}[quarter]
    return f"{year:04d}-{month:02d}-01"


def quarter_label(q: Any) -> str | None:
    if pd.isna(q):
        return None
    year, quarter = quarter_to_tuple(str(q))
    return f"{year}-Q{quarter}"


def current_quarter_code() -> int:
    today = date.today()
    quarter = (today.month - 1) // 3 + 1
    return int(f"{today.year}{quarter}")


def make_quarters(start_q: int, end_q: int) -> list[int]:
    sy, sq = quarter_to_tuple(start_q)
    ey, eq = quarter_to_tuple(end_q)
    if quarter_index(start_q) > quarter_index(end_q):
        raise ValueError("start-quarter가 end-quarter보다 늦습니다.")

    result = []
    y, q = sy, sq
    while (y, q) <= (ey, eq):
        result.append(int(f"{y}{q}"))
        q += 1
        if q == 5:
            y += 1
            q = 1
    return result


def to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def clean_str_series(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip()


def none_if_nan(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if np.isnan(value) or np.isinf(value):
            return None
        return float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if pd.isna(value):
        return None
    return value


def safe_float(value: Any) -> float | None:
    value = none_if_nan(value)
    return None if value is None else float(value)


def safe_int(value: Any) -> int | None:
    value = none_if_nan(value)
    return None if value is None else int(round(float(value)))


def json_clean(obj: dict[str, Any]) -> dict[str, Any]:
    return {k: none_if_nan(v) for k, v in obj.items()}


def require_columns(df: pd.DataFrame, cols: Iterable[str], dataset_name: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"[{dataset_name}] 필요한 컬럼이 없습니다: {missing}\n"
            f"현재 컬럼 예시: {list(df.columns)[:40]}"
        )


# ---------------------------------------------------------------------
# 서울시 API
# ---------------------------------------------------------------------

def build_http_session() -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "AlleyCompass-ETL/1.0"})
    return session


def api_url(
    api_key: str,
    service: str,
    start: int,
    end: int,
    quarter: int | None = None,
) -> str:
    url = f"{SEOUL_API_BASE}/{api_key}/json/{service}/{start}/{end}"
    if quarter is not None:
        url += f"/{quarter}"
    return url


def parse_api_payload(payload: dict[str, Any], service: str) -> tuple[int, list[dict[str, Any]]]:
    """
    성공:
      {service: {"list_total_count": ..., "RESULT": ..., "row": [...]}}
    조회 결과 없음/오류:
      {"RESULT": {"CODE": "...", "MESSAGE": "..."}}
    """
    if service in payload:
        block = payload[service]
        result = block.get("RESULT", {})
        code = result.get("CODE")
        if code not in (None, "INFO-000"):
            raise RuntimeError(f"서울시 API 오류 [{code}] {result.get('MESSAGE')}")
        total = int(block.get("list_total_count", 0))
        rows = block.get("row", []) or []
        return total, rows

    result = payload.get("RESULT", {})
    code = result.get("CODE")
    message = result.get("MESSAGE", "")

    # INFO-200은 조회 결과 없음
    if code == "INFO-200":
        return 0, []

    raise RuntimeError(
        f"서울시 API 응답 형식을 확인할 수 없습니다. "
        f"code={code}, message={message}, keys={list(payload.keys())}"
    )


def fetch_page(
    session: requests.Session,
    api_key: str,
    service: str,
    start: int,
    end: int,
    quarter: int | None,
    timeout: int = 60,
) -> tuple[int, list[dict[str, Any]]]:
    url = api_url(api_key, service, start, end, quarter)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()

    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"JSON 파싱 실패: {url}\n응답 앞부분: {response.text[:500]}"
        ) from exc

    return parse_api_payload(payload, service)


def fetch_service(
    session: requests.Session,
    api_key: str,
    service: str,
    quarter: int | None = None,
) -> pd.DataFrame:
    """
    한 서비스(또는 한 분기)를 1,000건씩 자동 페이징하여 DataFrame으로 반환.
    """
    total, rows = fetch_page(
        session=session,
        api_key=api_key,
        service=service,
        start=1,
        end=PAGE_SIZE,
        quarter=quarter,
    )

    if total == 0:
        return pd.DataFrame()

    all_rows = list(rows)
    log(f"{service} {quarter or 'ALL'}: 총 {total:,}건")

    start = PAGE_SIZE + 1
    while start <= total:
        end = min(start + PAGE_SIZE - 1, total)
        _, page_rows = fetch_page(
            session=session,
            api_key=api_key,
            service=service,
            start=start,
            end=end,
            quarter=quarter,
        )
        all_rows.extend(page_rows)
        start = end + 1
        time.sleep(0.05)

    return pd.DataFrame(all_rows)


# ---------------------------------------------------------------------
# RAW 다운로드 / 캐시
# ---------------------------------------------------------------------

@dataclass
class Paths:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def processed(self) -> Path:
        return self.root / "processed"

    def ensure(self) -> None:
        self.raw.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)
        for key in SERVICES:
            (self.raw / key).mkdir(parents=True, exist_ok=True)


def raw_path(paths: Paths, key: str, quarter: int | None) -> Path:
    name = f"{quarter}.csv" if quarter is not None else "all.csv"
    return paths.raw / key / name


def download_raw_data(
    api_key: str,
    quarters: list[int],
    paths: Paths,
    refresh: bool,
) -> None:
    session = build_http_session()

    for key, cfg in SERVICES.items():
        service = cfg["service"]

        if cfg["quarter_filter"]:
            for q in quarters:
                out = raw_path(paths, key, q)
                if out.exists() and not refresh:
                    log(f"캐시 사용: {out}")
                    continue

                log(f"다운로드: {key} / {q}")
                df = fetch_service(session, api_key, service, quarter=q)

                if df.empty:
                    log(f"  -> 데이터 없음: {key} / {q}")
                    # 빈 파일도 남겨 두어 재실행 시 같은 요청을 반복하지 않게 함
                    pd.DataFrame().to_csv(out, index=False)
                else:
                    df.to_csv(out, index=False, encoding="utf-8-sig")
                    log(f"  -> 저장: {out} ({len(df):,} rows)")
        else:
            out = raw_path(paths, key, None)
            if out.exists() and not refresh:
                log(f"캐시 사용: {out}")
                continue

            log(f"다운로드: {key} / 전체")
            df = fetch_service(session, api_key, service, quarter=None)
            if df.empty:
                raise RuntimeError(f"{key} 서비스에서 데이터를 받지 못했습니다.")
            df.to_csv(out, index=False, encoding="utf-8-sig")
            log(f"  -> 저장: {out} ({len(df):,} rows)")


def read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype=str, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    return df


def load_quartered(paths: Paths, key: str, quarters: list[int]) -> pd.DataFrame:
    frames = []
    for q in quarters:
        p = raw_path(paths, key, q)
        df = read_csv_safe(p)
        if not df.empty:
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_unfiltered(paths: Paths, key: str, start_q: int, end_q: int) -> pd.DataFrame:
    df = read_csv_safe(raw_path(paths, key, None))
    if df.empty:
        return df

    require_columns(df, ["STDR_YYQU_CD"], key)
    q = pd.to_numeric(df["STDR_YYQU_CD"], errors="coerce")
    lo, hi = quarter_index(start_q), quarter_index(end_q)

    q_idx = q.map(lambda x: quarter_index(int(x)) if pd.notna(x) else np.nan)
    return df[(q_idx >= lo) & (q_idx <= hi)].copy()


# ---------------------------------------------------------------------
# 전처리
# ---------------------------------------------------------------------

def prep_stores(df: pd.DataFrame) -> pd.DataFrame:
    require_columns(
        df,
        [
            "STDR_YYQU_CD", "TRDAR_CD", "TRDAR_CD_NM",
            "SVC_INDUTY_CD", "SVC_INDUTY_CD_NM",
            "SIMILR_INDUTY_STOR_CO", "OPBIZ_RT", "CLSBIZ_RT",
        ],
        "stores",
    )

    out = pd.DataFrame({
        "quarter": clean_str_series(df["STDR_YYQU_CD"]),
        "district_code": clean_str_series(df["TRDAR_CD"]),
        "district_name": clean_str_series(df["TRDAR_CD_NM"]),
        "business_code": clean_str_series(df["SVC_INDUTY_CD"]),
        "business_name": clean_str_series(df["SVC_INDUTY_CD_NM"]),
        "store_count": to_num(df["SIMILR_INDUTY_STOR_CO"]),
        "opening_rate": to_num(df["OPBIZ_RT"]),
        "closure_rate": to_num(df["CLSBIZ_RT"]),
        "franchise_store_count": to_num(df.get("FRC_STOR_CO", pd.Series(index=df.index, dtype=float))),
        "normal_store_count": to_num(df.get("STOR_CO", pd.Series(index=df.index, dtype=float))),
        "open_store_count": to_num(df.get("OPBIZ_STOR_CO", pd.Series(index=df.index, dtype=float))),
        "close_store_count": to_num(df.get("CLSBIZ_STOR_CO", pd.Series(index=df.index, dtype=float))),
    })

    return out.dropna(subset=["quarter", "district_code", "business_code"])


def prep_sales(df: pd.DataFrame) -> pd.DataFrame:
    require_columns(
        df,
        [
            "STDR_YYQU_CD", "TRDAR_CD", "SVC_INDUTY_CD",
            "SVC_INDUTY_CD_NM", "THSMON_SELNG_AMT",
        ],
        "sales",
    )

    def n(name: str) -> pd.Series:
        return to_num(df.get(name, pd.Series(index=df.index, dtype=float)))

    out = pd.DataFrame({
        "quarter": clean_str_series(df["STDR_YYQU_CD"]),
        "district_code": clean_str_series(df["TRDAR_CD"]),
        "business_code": clean_str_series(df["SVC_INDUTY_CD"]),
        "sales_business_name": clean_str_series(df["SVC_INDUTY_CD_NM"]),
        "estimated_sales": n("THSMON_SELNG_AMT"),
        "sales_count": n("THSMON_SELNG_CO"),
        "weekday_sales": n("MDWK_SELNG_AMT"),
        "weekend_sales": n("WKEND_SELNG_AMT"),
        "sales_age20": n("AGRDE_20_SELNG_AMT"),
        "sales_age30": n("AGRDE_30_SELNG_AMT"),
        "sales_00_06": n("TMZON_00_06_SELNG_AMT"),
        "sales_06_11": n("TMZON_06_11_SELNG_AMT"),
        "sales_11_14": n("TMZON_11_14_SELNG_AMT"),
        "sales_14_17": n("TMZON_14_17_SELNG_AMT"),
        "sales_17_21": n("TMZON_17_21_SELNG_AMT"),
        "sales_21_24": n("TMZON_21_24_SELNG_AMT"),
    })

    return out.dropna(subset=["quarter", "district_code", "business_code"])


def prep_foot(df: pd.DataFrame) -> pd.DataFrame:
    require_columns(
        df,
        [
            "STDR_YYQU_CD", "TRDAR_CD", "TRDAR_CD_NM",
            "TOT_FLPOP_CO", "AGRDE_20_FLPOP_CO", "AGRDE_30_FLPOP_CO",
        ],
        "foot",
    )

    def n(name: str) -> pd.Series:
        return to_num(df.get(name, pd.Series(index=df.index, dtype=float)))

    out = pd.DataFrame({
        "quarter": clean_str_series(df["STDR_YYQU_CD"]),
        "district_code": clean_str_series(df["TRDAR_CD"]),
        "foot_district_name": clean_str_series(df["TRDAR_CD_NM"]),
        "foot_traffic": n("TOT_FLPOP_CO"),
        "foot_traffic_20": n("AGRDE_20_FLPOP_CO"),
        "foot_traffic_30": n("AGRDE_30_FLPOP_CO"),
        "foot_00_06": n("TMZON_00_06_FLPOP_CO"),
        "foot_06_11": n("TMZON_06_11_FLPOP_CO"),
        "foot_11_14": n("TMZON_11_14_FLPOP_CO"),
        "foot_14_17": n("TMZON_14_17_FLPOP_CO"),
        "foot_17_21": n("TMZON_17_21_FLPOP_CO"),
        "foot_21_24": n("TMZON_21_24_FLPOP_CO"),
    })

    return (
        out.dropna(subset=["quarter", "district_code"])
        .drop_duplicates(["quarter", "district_code"], keep="last")
    )


def prep_worker(df: pd.DataFrame) -> pd.DataFrame:
    require_columns(
        df,
        ["STDR_YYQU_CD", "TRDAR_CD", "TOT_WRC_POPLTN_CO"],
        "worker",
    )

    def n(name: str) -> pd.Series:
        return to_num(df.get(name, pd.Series(index=df.index, dtype=float)))

    out = pd.DataFrame({
        "quarter": clean_str_series(df["STDR_YYQU_CD"]),
        "district_code": clean_str_series(df["TRDAR_CD"]),
        "worker_population": n("TOT_WRC_POPLTN_CO"),
        "worker_age20": n("AGRDE_20_WRC_POPLTN_CO"),
        "worker_age30": n("AGRDE_30_WRC_POPLTN_CO"),
    })
    return (
        out.dropna(subset=["quarter", "district_code"])
        .drop_duplicates(["quarter", "district_code"], keep="last")
    )


def prep_resident(df: pd.DataFrame) -> pd.DataFrame:
    require_columns(
        df,
        ["STDR_YYQU_CD", "TRDAR_CD", "TOT_REPOP_CO"],
        "resident",
    )

    def n(name: str) -> pd.Series:
        return to_num(df.get(name, pd.Series(index=df.index, dtype=float)))

    out = pd.DataFrame({
        "quarter": clean_str_series(df["STDR_YYQU_CD"]),
        "district_code": clean_str_series(df["TRDAR_CD"]),
        "resident_population": n("TOT_REPOP_CO"),
        "resident_age20": n("AGRDE_20_REPOP_CO"),
        "resident_age30": n("AGRDE_30_REPOP_CO"),
        "household_count": n("TOT_HSHLD_CO"),
        "apartment_households": n("APT_HSHLD_CO"),
    })
    return (
        out.dropna(subset=["quarter", "district_code"])
        .drop_duplicates(["quarter", "district_code"], keep="last")
    )


def prep_facilities(df: pd.DataFrame) -> pd.DataFrame:
    require_columns(
        df,
        ["STDR_YYQU_CD", "TRDAR_CD", "VIATR_FCLTY_CO"],
        "facilities",
    )

    def n(name: str) -> pd.Series:
        return to_num(df.get(name, pd.Series(index=df.index, dtype=float)))

    out = pd.DataFrame({
        "quarter": clean_str_series(df["STDR_YYQU_CD"]),
        "district_code": clean_str_series(df["TRDAR_CD"]),
        "facility_count": n("VIATR_FCLTY_CO"),
        "subway_station_count": n("SUBWAY_STATN_CO"),
        "bus_stop_count": n("BUS_STTN_CO"),
        "rail_station_count": n("RLROAD_STATN_CO"),
        "university_count": n("UNIV_CO"),
        "hospital_count": n("GNRL_HSPTL_CO"),
        "theater_count": n("THEAT_CO"),
        "supermarket_count": n("SUPMK_CO"),
    })

    out = (
        out.dropna(subset=["quarter", "district_code"])
        .drop_duplicates(["quarter", "district_code"], keep="last")
    )

    # 교통 접근성의 간단한 deterministic 파생값.
    # 절대적인 "교통점수"가 아니라 같은 분기 내 상대 percentile 점수이다.
    raw = (
        out["subway_station_count"].fillna(0) * 5
        + out["rail_station_count"].fillna(0) * 3
        + out["bus_stop_count"].fillna(0)
    )
    out["transit_raw"] = raw
    out["transit_score"] = (
        out.groupby("quarter")["transit_raw"]
        .rank(method="average", pct=True)
        .mul(100)
    )
    return out


def filter_businesses(
    stores: pd.DataFrame,
    business_names: list[str],
    business_codes: list[str],
) -> pd.DataFrame:
    if not business_names and not business_codes:
        log(
            "주의: 업종 필터가 없습니다. 서울시 전체 생활밀접업종을 처리하므로 "
            "district_features가 매우 커질 수 있습니다."
        )
        return stores

    mask = pd.Series(False, index=stores.index)
    if business_names:
        mask |= stores["business_name"].isin(business_names)
    if business_codes:
        mask |= stores["business_code"].isin(business_codes)

    filtered = stores[mask].copy()

    if filtered.empty:
        available = sorted(stores["business_name"].dropna().unique().tolist())
        raise RuntimeError(
            "요청한 업종을 찾지 못했습니다.\n"
            f"요청 이름={business_names}, 코드={business_codes}\n"
            f"사용 가능한 업종명 예시={available[:100]}"
        )

    found = (
        filtered[["business_code", "business_name"]]
        .drop_duplicates()
        .sort_values(["business_name", "business_code"])
    )
    log("선택 업종:\n" + found.to_string(index=False))
    return filtered


def build_feature_table(
    stores_raw: pd.DataFrame,
    sales_raw: pd.DataFrame,
    foot_raw: pd.DataFrame,
    worker_raw: pd.DataFrame,
    resident_raw: pd.DataFrame,
    facilities_raw: pd.DataFrame,
    business_names: list[str],
    business_codes: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stores = prep_stores(stores_raw)
    stores = filter_businesses(stores, business_names, business_codes)

    selected_codes = set(stores["business_code"].dropna().astype(str))
    sales = prep_sales(sales_raw)
    sales = sales[sales["business_code"].isin(selected_codes)].copy()

    foot = prep_foot(foot_raw)
    worker = prep_worker(worker_raw)
    resident = prep_resident(resident_raw)
    facilities = prep_facilities(facilities_raw)

    # 상권×업종×분기 기준 base는 점포 데이터.
    # 점포가 있는데 카드매출이 없는 경우도 보존하기 위해 stores LEFT JOIN sales.
    keys = ["quarter", "district_code", "business_code"]
    base = stores.merge(
        sales,
        on=keys,
        how="left",
        validate="one_to_one",
    )

    base["business_name"] = base["business_name"].fillna(base["sales_business_name"])
    base = base.drop(columns=["sales_business_name"], errors="ignore")

    # 상권 단위 데이터 결합
    for source in (foot, worker, resident, facilities):
        base = base.merge(
            source,
            on=["quarter", "district_code"],
            how="left",
            validate="many_to_one",
        )

    base["district_name"] = base["district_name"].fillna(base.get("foot_district_name"))
    base = base.drop(columns=["foot_district_name"], errors="ignore")

    # quarter 순서
    base["q_index"] = base["quarter"].astype(int).map(quarter_index)
    base = base.sort_values(["district_code", "business_code", "q_index"]).reset_index(drop=True)

    # QoQ 매출 성장률:
    # 직전 행이 실제 직전 분기일 때만 계산하여 시점 gap을 건너뛰지 않도록 한다.
    grp = base.groupby(["district_code", "business_code"], sort=False)
    prev_sales = grp["estimated_sales"].shift(1)
    prev_q = grp["q_index"].shift(1)
    consecutive = (base["q_index"] - prev_q) == 1

    base["sales_growth_rate"] = np.where(
        consecutive & prev_sales.notna() & (prev_sales != 0),
        (base["estimated_sales"] - prev_sales) / prev_sales,
        np.nan,
    )

    base["reference_date"] = base["quarter"].map(quarter_start_date)

    # 상권 면적 데이터가 없으므로 "점포수/면적" 밀도는 여전히 만들지 않는다.
    base["competition_density"] = np.nan

    # 대신 면적이 필요 없는 경쟁강도를 파생한다.
    #   점포당 배후수요 = (유동인구 + 상주인구 + 직장인구) / 동종업종 점포수
    # 값이 클수록 점포 하나가 나눠 갖는 수요가 커서 경쟁이 여유롭다.
    # competition_density 컬럼은 "면적 기반 밀도"를 뜻하므로 여기 넣지 않고
    # extra_features 로 보낸다. verification_tools.competition_density() 와
    # 웹 프론트(web/src/lib/scoring.js)가 같은 정의를 쓴다.
    demand_total = (
        base["foot_traffic"].fillna(0)
        + base["resident_population"].fillna(0)
        + base["worker_population"].fillna(0)
    )
    stores = base["store_count"].where(base["store_count"] > 0)
    base["backing_demand"] = demand_total.where(demand_total > 0)
    base["demand_per_store"] = base["backing_demand"] / stores

    # source_dates:
    # 각 값이 실제로 존재하는 경우에만 해당 분기를 기록.
    def source_date_dict(row: pd.Series) -> dict[str, Any]:
        q = quarter_label(row["quarter"])
        return {
            "stores": q if pd.notna(row.get("store_count")) else None,
            "sales": q if pd.notna(row.get("estimated_sales")) else None,
            "foot_traffic": q if pd.notna(row.get("foot_traffic")) else None,
            "worker_population": q if pd.notna(row.get("worker_population")) else None,
            "resident_population": q if pd.notna(row.get("resident_population")) else None,
            "facilities": q if pd.notna(row.get("facility_count")) else None,
        }

    base["source_dates"] = base.apply(source_date_dict, axis=1)

    # 마스터: 상권
    districts = (
        base[["district_code", "district_name", "q_index"]]
        .dropna(subset=["district_code", "district_name"])
        .sort_values("q_index")
        .drop_duplicates("district_code", keep="last")
        [["district_code", "district_name"]]
        .sort_values("district_code")
        .reset_index(drop=True)
    )

    # 마스터: 업종
    businesses = (
        base[["business_code", "business_name"]]
        .dropna()
        .drop_duplicates("business_code", keep="last")
        .sort_values("business_code")
        .reset_index(drop=True)
    )

    return base, districts, businesses


# ---------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------

def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL", "").strip()
    secret = (
        os.getenv("SUPABASE_SECRET_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )

    if not url:
        raise RuntimeError("SUPABASE_URL이 .env에 없습니다.")
    if not secret:
        raise RuntimeError(
            "SUPABASE_SECRET_KEY가 .env에 없습니다. "
            "(구형 프로젝트라면 SUPABASE_SERVICE_ROLE_KEY도 지원합니다.)"
        )

    return create_client(url, secret)


def chunks(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def upsert_batches(
    supabase: Client,
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
    batch_size: int,
) -> None:
    if not rows:
        return

    total = len(rows)
    done = 0
    for batch in chunks(rows, batch_size):
        (
            supabase.table(table)
            .upsert(
                batch,
                on_conflict=on_conflict,
                returning="minimal",
                default_to_null=False,
            )
            .execute()
        )
        done += len(batch)
        log(f"Supabase {table}: {done:,}/{total:,}")


def select_all(
    supabase: Client,
    table: str,
    columns: str,
    page_size: int = 1000,
    max_workers: int = 6,
) -> list[dict[str, Any]]:
    """전체 행을 id 구간별로 나눠 병렬 조회한다.

    처음엔 OFFSET(.range())으로 페이지를 나눠 순서대로(또는 동시에) 요청했는데,
    district_features(14만 행 이상)에서 실제로 문제가 났다 — OFFSET이 깊어질수록
    Postgres가 그 앞의 행을 전부 스캔해야 해서, select("*")처럼 폭이 넓은
    조회는 뒤쪽 페이지에서 Supabase의 statement_timeout을 넘겨 실패했다
    (offset 0은 1초, offset 100000은 타임아웃 — 직접 재현해 확인함).

    대신 id(bigint identity, 기본키) 구간으로 나눠서 각 구간을 gt/lte
    범위 조건으로 직접 조회한다 — 인덱스로 바로 찾아가므로 구간 위치와
    무관하게 빠르고, 구간마다 완전히 독립적이라 동시에 여러 개를 보내도
    안전하다.

    max_workers를 낮게 잡은 이유: Render 무료 플랜처럼 CPU가 아주 약한
    환경(사실상 0.1 vCPU급)에서는 너무 많은 스레드가 서로 자원을 다투다가
    개별 요청이 오히려 statement_timeout을 넘기는 걸 실제로 겪었다(로컬
    macOS에선 12로도 문제없었지만 Render에서는 실패했다). 재시도 횟수와
    대기 시간도 그래서 넉넉히 뒀다.
    """
    probe = (
        supabase.table(table)
        .select("id", count="exact")
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    total = probe.count or 0
    if total == 0:
        return []
    max_id = probe.data[0]["id"] if probe.data else 0

    n_chunks = max(1, math.ceil(max_id / page_size))
    bounds = [(i * page_size, min((i + 1) * page_size, max_id)) for i in range(n_chunks)]

    def fetch_chunk(bound: tuple[int, int], retries: int = 5) -> list[dict[str, Any]]:
        lo, hi = bound
        for attempt in range(retries):
            try:
                resp = (
                    supabase.table(table)
                    .select(columns)
                    .gt("id", lo)
                    .lte("id", hi)
                    .execute()
                )
                return resp.data or []
            except Exception:  # noqa: BLE001 — 일시적 부하 등, 마지막 시도면 그대로 올린다
                if attempt == retries - 1:
                    raise
                time.sleep(min(1.5 * (attempt + 1), 6.0))
        return []  # 도달하지 않음(mypy 안심용)

    result: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for page in executor.map(fetch_chunk, bounds):
            result.extend(page)

    return result


def upload_masters(
    supabase: Client,
    districts: pd.DataFrame,
    businesses: pd.DataFrame,
    batch_size: int,
) -> tuple[dict[str, int], dict[str, int]]:
    district_rows = [
        {
            "district_code": str(r.district_code),
            "district_name": str(r.district_name),
            # gu_name / latitude / longitude는 이번 5종 데이터에 없으므로 건드리지 않음
        }
        for r in districts.itertuples(index=False)
    ]

    business_rows = [
        {
            "business_code": str(r.business_code),
            "business_name": str(r.business_name),
        }
        for r in businesses.itertuples(index=False)
    ]

    upsert_batches(
        supabase,
        "districts",
        district_rows,
        on_conflict="district_code",
        batch_size=batch_size,
    )
    upsert_batches(
        supabase,
        "business_types",
        business_rows,
        on_conflict="business_code",
        batch_size=batch_size,
    )

    district_data = select_all(supabase, "districts", "id,district_code")
    business_data = select_all(supabase, "business_types", "id,business_code")

    district_map = {str(x["district_code"]): int(x["id"]) for x in district_data}
    business_map = {str(x["business_code"]): int(x["id"]) for x in business_data}

    return district_map, business_map


def build_feature_records(
    base: pd.DataFrame,
    district_map: dict[str, int],
    business_map: dict[str, int],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    extra_cols = [
        "franchise_store_count",
        "normal_store_count",
        "open_store_count",
        "close_store_count",
        "sales_count",
        "weekday_sales",
        "weekend_sales",
        "sales_age20",
        "sales_age30",
        "sales_00_06",
        "sales_06_11",
        "sales_11_14",
        "sales_14_17",
        "sales_17_21",
        "sales_21_24",
        "foot_00_06",
        "foot_06_11",
        "foot_11_14",
        "foot_14_17",
        "foot_17_21",
        "foot_21_24",
        "worker_age20",
        "worker_age30",
        "resident_age20",
        "resident_age30",
        "household_count",
        "apartment_households",
        "subway_station_count",
        "bus_stop_count",
        "rail_station_count",
        "university_count",
        "hospital_count",
        "theater_count",
        "supermarket_count",
        "transit_raw",
        "backing_demand",
        "demand_per_store",
    ]

    for row in base.to_dict(orient="records"):
        dcode = str(row["district_code"])
        bcode = str(row["business_code"])

        if dcode not in district_map:
            raise RuntimeError(f"districts 매핑 실패: {dcode}")
        if bcode not in business_map:
            raise RuntimeError(f"business_types 매핑 실패: {bcode}")

        extra = json_clean({k: row.get(k) for k in extra_cols})

        record = {
            "district_id": district_map[dcode],
            "business_type_id": business_map[bcode],
            "reference_date": row["reference_date"],

            "foot_traffic": safe_float(row.get("foot_traffic")),
            "foot_traffic_20": safe_float(row.get("foot_traffic_20")),
            "foot_traffic_30": safe_float(row.get("foot_traffic_30")),

            "resident_population": safe_float(row.get("resident_population")),
            "worker_population": safe_float(row.get("worker_population")),

            "store_count": safe_int(row.get("store_count")),
            "opening_rate": safe_float(row.get("opening_rate")),
            "closure_rate": safe_float(row.get("closure_rate")),

            "estimated_sales": safe_float(row.get("estimated_sales")),
            "sales_growth_rate": safe_float(row.get("sales_growth_rate")),

            # 상권 면적 데이터가 없으므로 NULL
            "competition_density": None,

            "facility_count": safe_int(row.get("facility_count")),
            "transit_score": safe_float(row.get("transit_score")),

            "extra_features": extra,
            "source_dates": row["source_dates"],
        }

        records.append(record)

    return records


def upload_features(
    supabase: Client,
    base: pd.DataFrame,
    districts: pd.DataFrame,
    businesses: pd.DataFrame,
    batch_size: int,
) -> None:
    district_map, business_map = upload_masters(
        supabase=supabase,
        districts=districts,
        businesses=businesses,
        batch_size=batch_size,
    )

    records = build_feature_records(base, district_map, business_map)
    upsert_batches(
        supabase,
        "district_features",
        records,
        on_conflict="district_id,business_type_id,reference_date",
        batch_size=batch_size,
    )


# ---------------------------------------------------------------------
# 데이터 품질 리포트
# ---------------------------------------------------------------------

def print_quality_report(base: pd.DataFrame) -> None:
    important = [
        "estimated_sales",
        "foot_traffic",
        "resident_population",
        "worker_population",
        "store_count",
        "closure_rate",
        "facility_count",
    ]

    print("\n" + "=" * 72)
    print("DATA QUALITY REPORT")
    print("=" * 72)
    print(f"Feature rows : {len(base):,}")
    print(f"Districts    : {base['district_code'].nunique():,}")
    print(f"Businesses   : {base['business_code'].nunique():,}")
    print(f"Quarters     : {base['quarter'].nunique():,}")
    print()

    for col in important:
        miss = base[col].isna().mean() * 100
        print(f"{col:24s} missing = {miss:6.2f}%")

    dup = base.duplicated(
        ["district_code", "business_code", "reference_date"]
    ).sum()
    print(f"\nUnique-key duplicates = {dup:,}")

    print("\n분기 범위:")
    print(sorted(base["quarter"].dropna().unique().tolist()))
    print("=" * 72 + "\n")


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="서울시 상권분석 데이터 -> 전처리 -> Supabase ETL"
    )

    parser.add_argument(
        "--start-quarter",
        type=int,
        default=MIN_SUPPORTED_QUARTER,
        help="시작 분기. 예: 20211",
    )
    parser.add_argument(
        "--end-quarter",
        type=int,
        default=current_quarter_code(),
        help="종료 분기. 예: 20252. 기본값은 현재 분기이며 데이터 없는 분기는 자동 건너뜀.",
    )
    parser.add_argument(
        "--business-name",
        action="append",
        default=[],
        help='사용할 서울시 서비스 업종명. 여러 개면 반복 입력. 예: --business-name "커피-음료"',
    )
    parser.add_argument(
        "--business-code",
        action="append",
        default=[],
        help="사용할 서울시 서비스 업종코드. 여러 개면 반복 입력.",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="RAW/processed 데이터 저장 디렉터리",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="기존 RAW CSV 캐시를 무시하고 다시 다운로드",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Supabase 업로드 없이 다운로드/전처리까지만 실행",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=300,
        help="Supabase upsert batch 크기",
    )

    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    if args.start_quarter < MIN_SUPPORTED_QUARTER:
        raise RuntimeError(
            "2026-07-03 이후 서울시 제공 기준 변경을 반영하여 "
            "이 스크립트는 2021년 이후 데이터만 사용합니다. "
            f"start-quarter >= {MIN_SUPPORTED_QUARTER} 로 지정하세요."
        )

    quarter_to_tuple(args.start_quarter)
    quarter_to_tuple(args.end_quarter)
    quarters = make_quarters(args.start_quarter, args.end_quarter)

    seoul_key = os.getenv("SEOUL_API_KEY", "").strip()
    if not seoul_key:
        raise RuntimeError("SEOUL_API_KEY가 .env에 없습니다.")

    paths = Paths(Path(args.data_dir))
    paths.ensure()

    log(
        f"실행 범위: {args.start_quarter} ~ {args.end_quarter} "
        f"({len(quarters)} quarters)"
    )

    # 1. 다운로드
    download_raw_data(
        api_key=seoul_key,
        quarters=quarters,
        paths=paths,
        refresh=args.refresh,
    )

    # 2. RAW 로드
    stores_raw = load_quartered(paths, "stores", quarters)
    sales_raw = load_quartered(paths, "sales", quarters)
    foot_raw = load_quartered(paths, "foot", quarters)

    worker_raw = load_unfiltered(
        paths, "worker", args.start_quarter, args.end_quarter
    )
    resident_raw = load_unfiltered(
        paths, "resident", args.start_quarter, args.end_quarter
    )
    facilities_raw = load_unfiltered(
        paths, "facilities", args.start_quarter, args.end_quarter
    )

    for name, df in {
        "stores": stores_raw,
        "sales": sales_raw,
        "foot": foot_raw,
        "worker": worker_raw,
        "resident": resident_raw,
        "facilities": facilities_raw,
    }.items():
        if df.empty:
            raise RuntimeError(f"{name} 데이터가 비어 있습니다.")
        log(f"RAW {name}: {len(df):,} rows")

    # 3. 전처리 / 결합
    base, districts, businesses = build_feature_table(
        stores_raw=stores_raw,
        sales_raw=sales_raw,
        foot_raw=foot_raw,
        worker_raw=worker_raw,
        resident_raw=resident_raw,
        facilities_raw=facilities_raw,
        business_names=args.business_name,
        business_codes=args.business_code,
    )

    if base.empty:
        raise RuntimeError("최종 feature table이 비었습니다.")

    print_quality_report(base)

    # 4. 로컬 processed 저장
    districts.to_csv(
        paths.processed / "districts.csv",
        index=False,
        encoding="utf-8-sig",
    )
    businesses.to_csv(
        paths.processed / "business_types.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # JSON 컬럼은 CSV에 문자열로 저장
    csv_base = base.copy()
    csv_base["source_dates"] = csv_base["source_dates"].map(
        lambda x: json.dumps(x, ensure_ascii=False)
    )
    csv_base.to_csv(
        paths.processed / "district_features_debug.csv",
        index=False,
        encoding="utf-8-sig",
    )

    log(f"전처리 완료: {paths.processed}")

    # 5. Supabase
    if args.no_upload:
        log("--no-upload 지정: Supabase 업로드 생략")
        return

    supabase = get_supabase_client()
    upload_features(
        supabase=supabase,
        base=base,
        districts=districts,
        businesses=businesses,
        batch_size=args.batch_size,
    )

    log("완료: districts / business_types / district_features 업로드 성공")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n사용자 중단")
        sys.exit(130)
    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        raise
