#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — LightGBM 생존 안정성 모델 학습
PRD §14~§15 구현

    Feature(district_features 다분기 패널)
        -> labels.build_labels()      Label 구성 (§7.1)
        -> features.build_feature_matrix()
        -> temporal_split()            과거 Train / 중간 Validation / 최근 Test (§15)
        -> LightGBM 학습
        -> ROC-AUC / PR-AUC / Brier / Calibration / Top-K 평가 (§15)
        -> model_versions 저장 (Supabase, --save-to-supabase)

⚠️ 실행 전에 알아야 할 것
    이 스크립트가 의미 있는 결과를 내려면 상권×업종×분기 패널이 최소
    수년치(권장: 2021Q1~) 필요하다. alley_compass_etl.py로 아직 1개
    분기만 받아둔 상태라면 --synthetic 데모만 돌아간다 — 그건 "이 코드가
    안 죽고 돈다"는 것만 증명하지, 실제 예측 성능을 의미하지 않는다.
    합성 데이터 실행 결과는 model_versions에 저장하지 않는다(아래
    _assert_not_synthetic_before_save 참고).

사용법
    # 1) 배관 점검 — 합성 데이터로 크래시/로직만 확인 (실데이터 불필요)
    python train.py --synthetic

    # 2) 실제 학습 — alley_compass_etl로 2021Q1~ 다분기 데이터를 먼저 모은 뒤
    python train.py --supabase --train-end-quarter 20224 --val-end-quarter 20234
    python train.py --csv ../alley_compass_etl/data/processed/district_features_full.csv \
        --train-end-quarter 20224 --val-end-quarter 20234 --save-to-supabase
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "alley_compass_etl"))
from verification_tools import load_feature_frame, log  # noqa: E402

from features import FEATURE_COLUMNS, build_feature_matrix  # noqa: E402
from labels import build_labels  # noqa: E402

MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_NAME = "LightGBM"


def temporal_split(meta: pd.DataFrame, train_end_q: int, val_end_q: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """PRD §15: 과거 Train / 그 이후 Validation / 가장 최근 Test.
    q_index(연도*4+분기) 기준으로 자르므로 미래 정보가 과거 쪽으로 새지 않는다."""
    if train_end_q >= val_end_q:
        raise ValueError("train_end_quarter는 val_end_quarter보다 앞이어야 합니다.")
    q = meta["q_index"]
    return (q <= train_end_q).to_numpy(), ((q > train_end_q) & (q <= val_end_q)).to_numpy(), (q > val_end_q).to_numpy()


def stability_score_from_proba(risk_proba: np.ndarray) -> np.ndarray:
    """predictions.stability_score(0~100). risk_proba는 '불안정할 확률'이므로 뒤집는다."""
    return np.round((1 - risk_proba) * 100, 1)


def _topk_lift(y_true: np.ndarray, risk_proba: np.ndarray, k_frac: float = 0.05) -> dict[str, Any] | None:
    """PRD §15 Top-K Evaluation: 모델이 안정적이라고 고른 상위 K가 실제로도
    평균보다 안정적인지. risk_proba가 낮을수록(=stability_score가 높을수록)
    추천 우선순위가 높다는 가정."""
    n = len(y_true)
    k = max(1, int(round(n * k_frac)))
    if n < 20:
        return None
    order = np.argsort(risk_proba)  # 위험 낮은 순 = 추천 우선순위 높은 순
    top_idx = order[:k]
    baseline_stable_rate = float((1 - y_true).mean())
    topk_stable_rate = float((1 - y_true[top_idx]).mean())
    return {
        "k": k,
        "k_frac": k_frac,
        "topk_stable_rate": round(topk_stable_rate, 4),
        "baseline_stable_rate": round(baseline_stable_rate, 4),
        "lift": None if baseline_stable_rate == 0 else round(topk_stable_rate / baseline_stable_rate, 3),
    }


def _eval_split(model: lgb.LGBMClassifier, X: pd.DataFrame, y: pd.Series, mask: np.ndarray) -> dict[str, Any] | None:
    if mask.sum() == 0:
        return None
    y_split = y[mask].to_numpy()
    proba = model.predict_proba(X[mask])[:, 1]
    metrics: dict[str, Any] = {"n": int(mask.sum()), "positive_rate": round(float(y_split.mean()), 4)}
    if len(set(y_split)) > 1:
        metrics["roc_auc"] = round(float(roc_auc_score(y_split, proba)), 4)
        metrics["pr_auc"] = round(float(average_precision_score(y_split, proba)), 4)
    else:
        metrics["roc_auc"] = None
        metrics["pr_auc"] = None
        log("  [경고] 이 구간엔 클래스가 하나뿐이라 ROC-AUC/PR-AUC를 계산할 수 없습니다.")
    metrics["brier_score"] = round(float(brier_score_loss(y_split, proba)), 4)

    if len(set(y_split)) > 1:
        frac_pos, mean_pred = calibration_curve(y_split, proba, n_bins=min(10, mask.sum()))
        metrics["calibration_error"] = round(float(np.mean(np.abs(frac_pos - mean_pred))), 4)
    else:
        metrics["calibration_error"] = None

    metrics["top_k"] = _topk_lift(y_split, proba)
    return metrics


@dataclass
class TrainResult:
    model: lgb.LGBMClassifier
    metrics: dict[str, Any]
    feature_importance: dict[str, float]
    train_start: str | None
    train_end: str | None
    val_start: str | None
    val_end: str | None
    test_start: str | None
    test_end: str | None
    is_synthetic: bool = False
    extra_metadata: dict[str, Any] = field(default_factory=dict)


def train_and_evaluate(
    panel: pd.DataFrame,
    train_end_q: int,
    val_end_q: int,
    *,
    horizon_quarters: int = 4,
    is_synthetic: bool = False,
    lgb_params: dict[str, Any] | None = None,
) -> TrainResult:
    labeled = build_labels(panel, horizon_quarters=horizon_quarters)
    X, y, meta = build_feature_matrix(labeled)
    if X.empty:
        raise ValueError(
            "라벨을 만들 수 있는 행이 없습니다 — 데이터가 horizon_quarters"
            f"({horizon_quarters}개 분기)보다 짧습니다. 다분기 데이터를 더 모으세요."
        )

    train_mask, val_mask, test_mask = temporal_split(meta, train_end_q, val_end_q)
    log(f"Temporal Split — train={train_mask.sum()}, val={val_mask.sum()}, test={test_mask.sum()}")
    if train_mask.sum() == 0:
        raise ValueError("학습 구간(train)에 데이터가 없습니다. train_end_quarter를 확인하세요.")

    params = dict(
        objective="binary",
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=15,
        min_child_samples=10,
        random_state=42,
        verbose=-1,
    )
    params.update(lgb_params or {})
    model = lgb.LGBMClassifier(**params)

    fit_kwargs: dict[str, Any] = {}
    if val_mask.sum() > 0 and len(set(y[val_mask])) > 1:
        fit_kwargs["eval_set"] = [(X[val_mask], y[val_mask])]
        fit_kwargs["callbacks"] = [lgb.early_stopping(30, verbose=False)]
    model.fit(X[train_mask], y[train_mask], **fit_kwargs)

    metrics = {
        "train": _eval_split(model, X, y, train_mask),
        "validation": _eval_split(model, X, y, val_mask),
        "test": _eval_split(model, X, y, test_mask),
    }
    importance = dict(zip(FEATURE_COLUMNS, [round(float(v), 1) for v in model.feature_importances_]))

    def period(mask: np.ndarray, col: str) -> str | None:
        return None if mask.sum() == 0 else str(meta.loc[mask, col].min())

    def period_end(mask: np.ndarray, col: str) -> str | None:
        return None if mask.sum() == 0 else str(meta.loc[mask, col].max())

    return TrainResult(
        model=model,
        metrics=metrics,
        feature_importance=importance,
        train_start=period(train_mask, "reference_date"),
        train_end=period_end(train_mask, "reference_date"),
        val_start=period(val_mask, "reference_date"),
        val_end=period_end(val_mask, "reference_date"),
        test_start=period(test_mask, "reference_date"),
        test_end=period_end(test_mask, "reference_date"),
        is_synthetic=is_synthetic,
        extra_metadata={"horizon_quarters": horizon_quarters, "n_features": len(FEATURE_COLUMNS)},
    )


def save_artifact(result: TrainResult, version: str) -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / f"{version}.joblib"
    joblib.dump(
        {
            "model": result.model,
            "feature_columns": FEATURE_COLUMNS,
            "metrics": result.metrics,
            "feature_importance": result.feature_importance,
            "is_synthetic": result.is_synthetic,
            "extra_metadata": result.extra_metadata,
        },
        path,
    )
    return path


def save_model_version_to_supabase(result: TrainResult, version: str) -> None:
    if result.is_synthetic:
        raise ValueError(
            "합성 데이터로 만든 모델은 model_versions에 저장하지 않습니다 "
            "(실제 성능이 아닌데 실제처럼 보이는 행이 DB에 남는 걸 막기 위함)."
        )
    from verification_tools import get_supabase_client

    supabase = get_supabase_client()
    test_metrics = result.metrics.get("test") or {}
    supabase.table("model_versions").insert(
        {
            "model_name": MODEL_NAME,
            "version": version,
            "target_name": "label_unstable (상권×업종 생존 안정성 결합 Label, PRD §7.1)",
            "roc_auc": test_metrics.get("roc_auc"),
            "pr_auc": test_metrics.get("pr_auc"),
            "brier_score": test_metrics.get("brier_score"),
            "calibration_error": test_metrics.get("calibration_error"),
            "train_start_date": result.train_start,
            "train_end_date": result.train_end,
            "validation_start_date": result.val_start,
            "validation_end_date": result.val_end,
            "feature_names": FEATURE_COLUMNS,
            "model_metadata": {
                **result.extra_metadata,
                "feature_importance": result.feature_importance,
                "full_metrics": result.metrics,
            },
        }
    ).execute()
    log(f"model_versions에 저장 완료 (version={version})")


# ---------------------------------------------------------------------
# 합성 데이터 — 배관 점검용. 실제 예측 성능을 의미하지 않는다.
# ---------------------------------------------------------------------

def make_synthetic_panel(n_districts: int = 80, n_quarters: int = 16, seed: int = 42) -> pd.DataFrame:
    """2021Q1부터 n_quarters개 분기 x n_districts개 상권의 합성 패널.

    각 상권에 고정된 '체질'(latent quality)을 주고, 폐업률/매출성장률이
    그 체질 + 노이즈로 나오게 만든다 — LightGBM이 신호를 실제로 잡아내는지
    (AUC가 0.5보다 유의하게 높은지) 확인하기 위한 최소 조건만 갖춘 데이터다.
    """
    rng = np.random.default_rng(seed)
    quarters = []
    y, q = 2021, 1
    for _ in range(n_quarters):
        quarters.append(y * 10 + q)
        q += 1
        if q == 5:
            y, q = y + 1, 1

    quality = rng.normal(0, 1, n_districts)  # 상권 고정 체질: 높을수록 좋음
    rows = []
    for d in range(n_districts):
        code = f"SYN{d:04d}"
        base_foot = rng.uniform(1000, 50000)
        for qi, quarter in enumerate(quarters):
            closure_rate = np.clip(8 - 3 * quality[d] + rng.normal(0, 1.5), 0.5, 30)
            sales_growth = np.clip(0.02 * quality[d] + rng.normal(0, 0.04), -0.5, 0.5)
            rows.append(
                {
                    "district_code": code,
                    "district_name": f"합성상권{d}",
                    "business_code": "SYN_BIZ",
                    "quarter": quarter,
                    "q_index": (quarter // 10) * 4 + (quarter % 10),
                    "reference_date": str(date(quarter // 10, 1 + 3 * ((quarter % 10) - 1), 1)),
                    "foot_traffic": base_foot * rng.uniform(0.9, 1.1),
                    "foot_traffic_20": base_foot * 0.3 * rng.uniform(0.8, 1.2),
                    "foot_traffic_30": base_foot * 0.25 * rng.uniform(0.8, 1.2),
                    "resident_population": rng.uniform(5000, 40000),
                    "worker_population": rng.uniform(2000, 30000),
                    "store_count": max(1, int(rng.uniform(5, 60))),
                    "opening_rate": np.clip(rng.normal(10, 3), 0, 40),
                    "closure_rate": closure_rate,
                    "estimated_sales": max(0, base_foot * 1000 * (1 + 0.1 * quality[d]) * rng.uniform(0.85, 1.15)),
                    "sales_growth_rate": sales_growth,
                    "facility_count": rng.integers(0, 20),
                    "transit_score": np.clip(rng.normal(50, 20), 0, 100),
                }
            )
    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="골목 컴퍼스 LightGBM 학습")
    p.add_argument("--csv", type=Path, default=None, help="다분기 district_features CSV (없으면 debug CSV)")
    p.add_argument("--supabase", action="store_true", help="Supabase district_features에서 로드")
    p.add_argument("--synthetic", action="store_true", help="합성 데이터로 배관 점검만 (실데이터 불필요)")
    p.add_argument("--train-end-quarter", type=int, default=None, help="예: 20224 (2022년 4분기까지 학습)")
    p.add_argument("--val-end-quarter", type=int, default=None, help="예: 20234 (2023년 4분기까지 검증)")
    p.add_argument("--horizon-quarters", type=int, default=4)
    p.add_argument("--version", default=None, help="model_versions.version (기본: v-YYYYMMDD-HHMM 또는 synthetic-...)")
    p.add_argument("--save-to-supabase", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.synthetic:
        panel = make_synthetic_panel()
        log(f"합성 패널 생성: {len(panel):,}행, 상권 {panel['district_code'].nunique()}개, "
            f"분기 {sorted(panel['q_index'].unique())[0]}~{sorted(panel['q_index'].unique())[-1]}")
        train_end_q = args.train_end_quarter or int(panel["q_index"].quantile(0.6))
        val_end_q = args.val_end_quarter or int(panel["q_index"].quantile(0.85))
        version = args.version or f"synthetic-{pd.Timestamp.now():%Y%m%d-%H%M}"
    else:
        panel = load_feature_frame(csv_path=args.csv, use_supabase=args.supabase)
        panel["q_index"] = pd.to_datetime(panel["reference_date"]).dt.year * 4 + (
            (pd.to_datetime(panel["reference_date"]).dt.month - 1) // 3 + 1
        )
        n_quarters = panel["q_index"].nunique()
        log(f"실데이터 로드: {len(panel):,}행, 분기 {n_quarters}개")
        if n_quarters < args.horizon_quarters + 2:
            print(
                f"오류: 분기가 {n_quarters}개뿐입니다. label horizon({args.horizon_quarters}개 분기)을 "
                "채우려면 최소 그보다 많은 분기가 필요합니다. 먼저 "
                "alley_compass_etl.py로 2021Q1 이후 여러 분기를 수집하세요 "
                "(예: --start-quarter 20211 --end-quarter 20252). "
                "지금 바로 배관만 확인하려면 --synthetic 을 쓰세요.",
                file=sys.stderr,
            )
            sys.exit(1)
        if args.train_end_quarter is None or args.val_end_quarter is None:
            print("오류: 실데이터 학습에는 --train-end-quarter 와 --val-end-quarter가 필요합니다.", file=sys.stderr)
            sys.exit(1)
        train_end_q, val_end_q = args.train_end_quarter, args.val_end_quarter
        version = args.version or f"v-{pd.Timestamp.now():%Y%m%d-%H%M}"

    result = train_and_evaluate(
        panel, train_end_q, val_end_q, horizon_quarters=args.horizon_quarters, is_synthetic=args.synthetic
    )

    log("=== 평가 결과 ===")
    print(json.dumps(result.metrics, ensure_ascii=False, indent=2))
    log("=== Feature Importance ===")
    for k, v in sorted(result.feature_importance.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")

    path = save_artifact(result, version)
    log(f"모델 저장: {path}" + (" (합성 데이터 — 실제 사용 금지)" if args.synthetic else ""))

    if args.save_to_supabase:
        save_model_version_to_supabase(result, version)


if __name__ == "__main__":
    main()
