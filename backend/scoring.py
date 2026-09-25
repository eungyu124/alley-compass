#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — 랭킹 스코어러
PRD §16 "사용자 Ranking Logic" 구현

    Model Survival Stability + Budget Fit + Target Customer Fit + User Preference
        -> Personalized Ranking

LightGBM 생존 안정성 Score(PRD §14)는 backend/models/ 에 올려둔 아티팩트가
있으면 그걸 쓰고, 없으면(모델을 아직 승격 안 한 로컬/개발 환경 등) 이
모듈이 원본 feature로 계산한 휴리스틱 Score로 조용히 대체한다 — 지어낸
예측을 만들지 않는다는 원칙과 같다. 실제로 쓰인 쪽에 따라 model_version이
"lightgbm-<버전>" 또는 "heuristic-v0"로 응답에 그대로 찍힌다.

모델 파일은 ml/train.py --save-to-supabase 로 model_versions에 등록한 것과
같은 버전 문자열로 backend/models/<version>.joblib 에 복사해 "승격"한다
(ml/models/ 은 실험용이라 gitignore 대상 — backend/models/ 만 커밋해서
Docker 이미지에 실린다). 피처 목록은 joblib 안에 같이 저장돼 있어(ml/
features.py의 FEATURE_COLUMNS) 여기서 ml/ 을 다시 import하지 않아도 된다.

Budget Fit: 5종 공식 데이터셋에 보증금/임대료가 없어(verification_tools.py
budget_validator 참고) 상권별로 검증 가능한 예산 적합도를 계산할 수 없다.
그래서 이 버전의 랭킹에는 예산을 반영하지 않는다 — 있지도 않은 임대료
데이터를 상권마다 다르게 지어내 순위에 반영하면 없는 데이터를 있는 것처럼
쓰는 것이기 때문이다. 프론트/CLI 단계의 "추정 보증금"(narrative_agents.
budget_margin fact)은 사용자가 직접 입력한 단일 추정치를 검증하는 용도로만
쓴다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import joblib
import pandas as pd

from verification_tools import demand_series

MODEL_VERSION = "heuristic-v0"

Age = Literal["20", "30", "both"]
Character = Literal["foot", "resident", "worker", "campus"]
Priority = Literal["survival", "cost", "growth"]

_BASE_WEIGHTS = {"demand": 0.30, "comp": 0.24, "perf": 0.16, "access": 0.12, "stability": 0.18}

# 표본 신뢰도 보정(베이지안 축소) 강도 — 업종 전체 점포수 중앙값에 비례.
# 처음엔 고정값(K=5)을 썼는데, 업종마다 점포수 규모 자체가 너무 달라서
# (커피-음료 중앙값 7개 vs 양식음식점 중앙값 3개) 하나의 고정값으론 안
# 맞았다: 커피-음료엔 과해서 원래 있던 점수 다양성이 거의 사라졌고
# (표준편차 21→8), 그런데도 양식음식점의 점포 1~2개짜리 상권은 여전히
# 상위권을 차지했다(K=5로도 못 막음). "이 업종 안에서 점포수가 어느
# 정도면 충분한 표본인가"가 업종마다 다르다는 뜻이라, 그 업종 자체의
# 중앙값에 비례한 강도를 쓴다 — STORE_COUNT_CONFIDENCE_K_FRAC * 중앙값.
STORE_COUNT_CONFIDENCE_K_FRAC = 1.0

# 그래도 점포 0~1개는 위 비례식만으론 안 막힐 수 있어(업종이 워낙 희소하면
# 중앙값 자체가 작아서 비례식의 K도 작아짐) 이 구간은 원점수를 아예 안 믿고
# 100% 업종 중앙값으로 대체한다 — "표본이 거의 없다"는 뜻이라 통계적 보정이
# 아니라 상식적인 하한선이다.
MIN_STORE_COUNT_ANY_TRUST = 2

_MODELS_DIR = Path(__file__).resolve().parent / "models"
_LGBM_CACHE: dict | None = None
_LGBM_LOAD_ATTEMPTED = False


def _load_lightgbm() -> dict | None:
    """backend/models/ 에 승격해 둔 LightGBM 아티팩트를 한 번만 불러와 캐싱한다.

    파일이 없으면 None — 호출부가 휴리스틱으로 조용히 대체한다. 여러 버전이
    있으면 파일명(v-YYYYMMDD-HHMM.joblib) 기준 가장 최신을 쓴다 — 이 형식이면
    문자열 정렬이 곧 시간 정렬이다.
    """
    global _LGBM_CACHE, _LGBM_LOAD_ATTEMPTED
    if _LGBM_LOAD_ATTEMPTED:
        return _LGBM_CACHE
    _LGBM_LOAD_ATTEMPTED = True

    if not _MODELS_DIR.exists():
        return None
    candidates = sorted(_MODELS_DIR.glob("*.joblib"))
    if not candidates:
        return None

    path = candidates[-1]
    try:
        artifact = joblib.load(path)
        artifact["version"] = path.stem
        _LGBM_CACHE = artifact
    except Exception as exc:  # noqa: BLE001 — 로드 실패는 휴리스틱 폴백으로 처리
        print(f"[경고] LightGBM 모델 로드 실패({path.name}): {exc}")
        _LGBM_CACHE = None
    return _LGBM_CACHE


def active_model_version() -> str:
    """지금 랭킹에 실제로 쓰이는 모델 버전. /health 처럼 스코어를 안 매기는
    곳에서도 "지금 어떤 모델이 붙어 있나"를 가볍게 확인할 때 쓴다."""
    artifact = _load_lightgbm()
    return f"lightgbm-{artifact['version']}" if artifact else MODEL_VERSION


def _lightgbm_stability(scope: pd.DataFrame) -> tuple[pd.Series | None, str]:
    """가능하면 (LightGBM 예측 Series, "lightgbm-<버전>")을, 안 되면 (None, MODEL_VERSION)을 돌려준다."""
    artifact = _load_lightgbm()
    if artifact is None:
        return None, MODEL_VERSION
    try:
        X = scope[artifact["feature_columns"]]
        proba_unstable = artifact["model"].predict_proba(X)[:, 1]
        score = pd.Series((1 - proba_unstable) * 100, index=scope.index).round(1)
        return score, f"lightgbm-{artifact['version']}"
    except Exception as exc:  # noqa: BLE001 — 예측 실패도 휴리스틱 폴백으로 처리
        print(f"[경고] LightGBM 예측 실패, 휴리스틱으로 대체: {exc}")
        return None, MODEL_VERSION


def _pct(series: pd.Series) -> pd.Series:
    """0~100 백분위. verification_tools.percentile()과 같은 정의
    (값이 클수록 백분위가 높다)."""
    return series.rank(pct=True, method="average") * 100


def _competition_score(scope: pd.DataFrame) -> pd.Series:
    """값이 클수록 경쟁이 여유롭다(=좋다)는 방향으로 통일한 0~100 점수.

    demand_series()로 배후수요를 만들 수 있으면 "점포당 배후수요" 백분위를,
    아니면(배후수요 컬럼이 전부 비었으면) 점포수가 적을수록 좋다는 뜻이므로
    점포수 백분위를 뒤집어서 쓴다. verification_tools.competition_density()와
    같은 정의다.
    """
    demand, _ = demand_series(scope)
    if demand is not None:
        stores = scope["store_count"].where(scope["store_count"] > 0)
        per_store = demand / stores
        return _pct(per_store)
    return 100 - _pct(scope["store_count"])


def _safe_pct(series: pd.Series) -> pd.Series | None:
    """전부 NaN이면(예: 분기가 1개뿐이라 성장률을 계산할 수 없는 경우)
    None을 돌려준다 — 중앙값으로 채워 넣지 않는다. 하나라도 값이 있으면
    나머지 NaN만 중앙값으로 채운 뒤 백분위를 계산한다."""
    if series.notna().sum() == 0:
        return None
    return _pct(series.fillna(series.median()))


def rank_districts(
    df: pd.DataFrame,
    business_code: str,
    *,
    age: Age = "both",
    character: Character = "foot",
    priority: Priority = "survival",
) -> tuple[pd.DataFrame, str]:
    """한 업종에 대해 서울 전체 상권을 조건에 맞게 재랭킹한다.

    구성 요소(demand/comp/perf/access/stability) 중 이번 데이터에 아예 없는
    지표는(예: 분기가 1개뿐이라 매출 성장률을 계산 못하는 경우) 중앙값 등으로
    지어내지 않고 가중치 계산에서 빼고 나머지로 재정규화한다 — 없는 데이터로
    점수를 오염시키지 않기 위해서다.

    반환: (rank/district_code/district_name/stability_score/target_fit_score/
    final_score/breakdown 컬럼을 가진 DataFrame, 사용된 기준시점)
    """
    scope = df[df["business_code"] == str(business_code)].copy()
    if scope.empty:
        raise ValueError(f"업종 코드 '{business_code}'에 해당하는 데이터가 없습니다.")

    as_of = scope["reference_date"].max()
    scope = (
        scope[scope["reference_date"] == as_of]
        .dropna(subset=["district_code", "district_name"])
        .drop_duplicates("district_code")
        .reset_index(drop=True)
    )

    foot20_pct = _pct(scope["foot_traffic_20"].fillna(scope["foot_traffic_20"].median()))
    foot30_pct = _pct(scope["foot_traffic_30"].fillna(scope["foot_traffic_30"].median()))
    resident_pct = _pct(scope["resident_population"].fillna(scope["resident_population"].median()))
    worker_pct = _pct(scope["worker_population"].fillna(scope["worker_population"].median()))

    demand_by_character = {
        "foot": 0.55 * foot20_pct + 0.45 * foot30_pct,
        "resident": resident_pct,
        "worker": worker_pct,
        "campus": 0.7 * foot20_pct + 0.3 * foot30_pct,
    }
    demand = demand_by_character[character]
    if age == "20":
        demand = 0.6 * demand + 0.4 * foot20_pct
    elif age == "30":
        demand = 0.6 * demand + 0.4 * foot30_pct

    comp = _competition_score(scope)
    comp = comp if comp.notna().any() else None

    components: dict[str, pd.Series | None] = {
        "demand": demand,
        "comp": comp,
        "perf": _safe_pct(scope["sales_growth_rate"]),
        "stability": (
            None
            if scope["closure_rate"].notna().sum() == 0
            else 100 - _pct(scope["closure_rate"].fillna(scope["closure_rate"].median()))
        ),
        "access": _safe_pct(0.6 * scope["transit_score"].fillna(0) + 0.4 * scope["facility_count"].fillna(0)),
    }

    weights = dict(_BASE_WEIGHTS)
    if priority == "survival":
        weights["stability"] *= 1.35
    elif priority == "growth":
        weights["perf"] *= 1.4

    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        raise ValueError("랭킹에 쓸 수 있는 지표가 하나도 없습니다 (데이터 부족).")
    w_avail = {k: weights[k] for k in available}
    total_w = sum(w_avail.values())
    w_avail = {k: v / total_w for k, v in w_avail.items()}

    heuristic_stability = sum(w_avail[k] * available[k] for k in available).round(1)
    stability_score = heuristic_stability

    # LightGBM이 승격돼 있으면 그 예측으로 헤드라인 점수를 바꿔치기한다.
    # breakdown(위 components)은 그대로 휴리스틱 축으로 남겨서 "어떤 요인이
    # 크게/작게 작용했는지" 설명은 계속 보여준다 — 바뀌는 건 최종 합산
    # 점수뿐이다. 모델이 없거나 예측이 실패하면 방금 계산한 휴리스틱
    # stability_score를 그대로 쓴다(폴백).
    lgbm_score, model_version = _lightgbm_stability(scope)
    if lgbm_score is not None:
        stability_score = lgbm_score

    # 표본 신뢰도 보정 — 어느 쪽으로 계산했든(LightGBM이든 휴리스틱 폴백이든)
    # 마지막에 한 번 더 건다. 점포가 1~2개뿐인 상권은 "폐업률이 학습 시점
    # 기준으로도 거의 항상 0%"라 LightGBM이 안정적으로 오인하고, 휴리스틱도
    # "수요÷점포수"라 점포수가 작을수록 값이 커져서 마찬가지로 부풀려지는 걸
    # 실측으로 확인했다(점포 1개짜리 상권이 강남역보다 안전하다고 나온 사례).
    # 두 계산 경로 모두 표본 크기를 반영하지 못하므로, 결과값에 한 번 더
    # 베이지안 축소를 적용한다.
    #
    # 강도는 이 업종의 점포수 중앙값에 비례시킨다 — 고정값(K=5)을 썼을 때는
    # 업종마다 점포수 규모가 완전히 달라 하나로 안 맞았다(커피-음료 중앙값
    # 7개엔 과해서 원래 있던 점수 다양성이 거의 사라졌고, 양식음식점 중앙값
    # 3개엔 부족해서 점포 1~2개짜리 상권이 여전히 상위권에 남았다 — 실측으로
    # 확인함). store_count가 이 하한(MIN_STORE_COUNT_ANY_TRUST) 미만이면
    # 비례식과 무관하게 원점수를 아예 안 믿는다 — 표본이 거의 없다는 뜻이라
    # 통계 보정이 아니라 상식적인 하한선이다.
    prior = float(stability_score.median())
    store_count = scope["store_count"].fillna(0)
    k_rel = max(store_count.median(), 1.0) * STORE_COUNT_CONFIDENCE_K_FRAC
    confidence = (store_count / (store_count + k_rel)).clip(upper=1.0)
    confidence = confidence.where(store_count >= MIN_STORE_COUNT_ANY_TRUST, 0.0)
    stability_score = (confidence * stability_score + (1 - confidence) * prior).round(1)

    out = scope[["district_code", "district_name"]].copy()

    # 지도 표시용 — CSV 소스(로컬 개발)에는 이 컬럼들이 아예 없을 수 있고,
    # Supabase 소스라도 아직 district_geo.py로 보강 안 된 상권은 NaN이다.
    # 둘 다 None으로 통일해서 내려보낸다(지어낸 좌표를 만들지 않는다).
    for col in ("gu_name", "latitude", "longitude", "area_m2"):
        if col not in scope.columns:
            out[col] = None
        else:
            out[col] = scope[col].where(scope[col].notna(), None)

    out["stability_score"] = stability_score
    out["target_fit_score"] = demand.round(1)
    # 예산 데이터가 없어 final_score = stability_score (§16 Budget Fit 항은 현재 미반영)
    out["final_score"] = out["stability_score"]

    n = len(scope)
    breakdown_cols = {
        k: (v.round(1).tolist() if v is not None else [None] * n) for k, v in components.items()
    }
    out["breakdown"] = [
        {
            "demand": breakdown_cols["demand"][i],
            "competition": breakdown_cols["comp"][i],
            "performance": breakdown_cols["perf"][i],
            "access": breakdown_cols["access"][i],
            "stability": breakdown_cols["stability"][i],
        }
        for i in range(n)
    ]

    out = out.sort_values("final_score", ascending=False).reset_index(drop=True)
    out["rank"] = out.index + 1
    out.attrs["missing_components"] = [k for k in components if k not in available]
    out.attrs["model_version"] = model_version
    return out, str(as_of)
