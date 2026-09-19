#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — 상권 좌표/구/면적 보강 (지도 표시용)

alley_compass_etl.py가 긁어오는 6종 데이터(유동인구·매출·점포·시설·직장인구·
상주인구)에는 좌표가 없다. 이 스크립트가 채우는 대상은 완전히 다른 API,
서울시 상권분석서비스의 "영역-상권"(TbgisTrdarRelm)이다.

    실시간 API를 직접 호출해 확인해보니 이 데이터셋은 이름과 달리 다각형
    경계가 아니라 중심점 좌표(XCNTS_VALUE/YDNTS_VALUE, EPSG:5181) + 면적
    (RELM_AR)만 준다. 진짜 다각형이 필요하면 별도 셰이프파일을 받아
    geopandas 등으로 처리해야 하지만, 지도에 마커/원(면적 비례)을 찍는
    데는 이걸로 충분하다고 판단해 이 방식으로 간다.

    구 이름(SIGNGU_CD_NM)도 같은 응답에 들어 있어 gu_name도 같이 채운다.

실행:
    python district_geo.py                 # 조회만, Supabase에 반영하지 않음
    python district_geo.py --upload        # districts 테이블에 반영

이미 alley_compass_etl.py로 한 번이라도 채워진 district_code에만 반영한다 —
어느 업종·분기에도 등장한 적 없는 상권까지 새로 만들지 않는다. "없는 데이터를
만들어내지 않는다"는 이 프로젝트 원칙이 상권 존재 여부에도 그대로 적용된다.

필요: db/schema_v1.1.sql의 v1.4 패치(area_m2 컬럼)를 먼저 Supabase에 적용해야
--upload가 성공한다.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from pyproj import Transformer

from alley_compass_etl import (
    build_http_session,
    fetch_service,
    get_supabase_client,
    log,
    select_all,
    upsert_batches,
)

SERVICE = "TbgisTrdarRelm"
SOURCE_EPSG = 5181  # 서울시 상권분석서비스 좌표계 (한국 중부원점 TM, 통칭 "중부원점")
TARGET_EPSG = 4326  # WGS84 — 카카오맵 등 지도 라이브러리가 쓰는 위경도


def fetch_district_geo(api_key: str) -> pd.DataFrame:
    """TbgisTrdarRelm 전량을 받아 district_code별 좌표/구/면적으로 정리한다."""
    session = build_http_session()
    df = fetch_service(session, api_key, SERVICE)
    if df.empty:
        raise RuntimeError(f"{SERVICE} 응답이 비어 있습니다.")

    transformer = Transformer.from_crs(SOURCE_EPSG, TARGET_EPSG, always_xy=True)
    x = df["XCNTS_VALUE"].astype(float).to_numpy()
    y = df["YDNTS_VALUE"].astype(float).to_numpy()
    lon, lat = transformer.transform(x, y)

    out = pd.DataFrame({
        "district_code": df["TRDAR_CD"].astype(str),
        "district_name": df["TRDAR_CD_NM"].astype(str),
        "gu_name": df["SIGNGU_CD_NM"].astype(str),
        "latitude": lat,
        "longitude": lon,
        "area_m2": df["RELM_AR"].astype(float),
    })
    # 같은 상권코드가 중복으로 잡히는 경우는 없어야 정상이지만, 방어적으로 정리한다.
    return out.drop_duplicates(subset="district_code")


def main() -> None:
    parser = argparse.ArgumentParser(description="상권 좌표/구/면적 보강 (지도 표시용)")
    parser.add_argument("--upload", action="store_true", help="Supabase districts 테이블에 반영")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    load_dotenv(Path(__file__).parent / ".env")
    api_key = os.getenv("SEOUL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SEOUL_API_KEY가 .env에 없습니다.")

    geo = fetch_district_geo(api_key)
    log(f"{SERVICE} 조회 완료: {len(geo):,}개 상권")

    if not args.upload:
        log("--upload 없이 실행됨 — Supabase에는 반영하지 않았습니다. 상위 10건만 미리보기:")
        print(geo.head(10).to_string(index=False))
        return

    supabase = get_supabase_client()
    existing = select_all(supabase, "districts", "district_code")
    existing_codes = {str(r["district_code"]) for r in existing}

    matched = geo[geo["district_code"].isin(existing_codes)]
    skipped = len(geo) - len(matched)
    log(
        f"기존 districts와 매칭: {len(matched):,}개 반영 예정 "
        f"(미매칭 {skipped:,}개는 아직 수집 안 된 상권이라 건너뜀)"
    )

    # district_name도 같이 보낸다 — NOT NULL 컬럼이라 하나. Postgres의
    # INSERT ... ON CONFLICT DO UPDATE는 이미 존재하는 행을 갱신할 때조차
    # "이 값으로 새로 넣는다면"의 후보 행을 먼저 만들어보고 NOT NULL을
    # 검사하므로, district_name을 빼면 이미 있는 상권이어도 실패한다.
    # 같은 원천(TRDAR_CD_NM)이라 기존 값과 사실상 같은 값으로 덮어쓸 뿐이다.
    rows = [
        {
            "district_code": r.district_code,
            "district_name": r.district_name,
            "gu_name": r.gu_name,
            "latitude": round(float(r.latitude), 6),
            "longitude": round(float(r.longitude), 6),
            "area_m2": float(r.area_m2),
        }
        for r in matched.itertuples(index=False)
    ]
    upsert_batches(supabase, "districts", rows, on_conflict="district_code", batch_size=args.batch_size)
    log("완료.")


if __name__ == "__main__":
    main()
