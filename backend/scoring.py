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

# 실측 25th percentile이 3이라(중앙값 7) 이 기준으로 빠지는 건 전체의 20% 이내다.
# 점포 1~2개인 상권은 폐업률이 태생적으로 0%나 100%밖에 못 나와서(중간이 없음)
# LightGBM이 "안정적"으로 오인하는 걸 재현해 확인했다 — rank_districts() 참고.
MIN_STORE_COUNT_FOR_MODEL = 3

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
        # 실측으로 확인한 문제: 점포가 1~2개뿐인 상권은 폐업률이 학습 시점
        # 기준으로도 거의 항상 0%다(망할 기회 자체가 적었을 뿐 검증된 안정성이
        # 아니다) — 그런데 모델이 이걸 "안정적"으로 오인해서, 점포 1개짜리
        # 신생 상권이 강남역보다 안전하다고 나오는 걸 직접 재현해 확인했다.
        # 표본이 이 기준보다 적은 행은 LightGBM 대신 휴리스틱(수요·경쟁·접근성
        # 등 여러 축의 가중 평균)으로 대체한다 — 휴리스틱은 closure_rate 하나에
        # 안 기대고 여러 축을 같이 보므로, 이런 상권의 낮은 수요·접근성이
        # 그대로 반영돼 점수가 과장되지 않는다. "표본 부족을 안정성으로
        # 둔갑시키지 않는다"는 원칙 — 재학습 없이 바로 적용 가능한 완화책이다.
        low_sample = scope["store_count"].fillna(0) < MIN_STORE_COUNT_FOR_MODEL
        stability_score = lgbm_score.where(~low_sample, heuristic_stability)
        if low_sample.any():
            model_version = f"{model_version}+heuristic-low-sample"

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
