#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — 전체 파이프라인 데모
PRD §9, §24 MVP Workflow 구현

    Feature(district_features)
        -> Fact Sheet (fact_sheet.py, 결정론적 코드)
        -> Recommendation Agent / Risk Agent (narrative_agents.py, Claude)
        -> Verification Agent (narrative_agents.verify_and_correct, 결정론적 코드)
        -> Verified Result

LightGBM 생존 안정성 Score는 아직 없으므로(§7.1 대기 중), 이 데모는
Feature 값 자체(유동인구·매출추세·경쟁밀도 등)로 추천/반대 근거를
생성한다. 모델 Score가 준비되면 fact_sheet에 한 줄 추가하면 된다.

사용법:
    python pipeline.py --dry-run                  # Claude 호출 없이 Fact Sheet만 확인
    python pipeline.py                             # 전체 실행 (ANTHROPIC_API_KEY 필요)
    python pipeline.py --district-code 3120014 --business-code CS100010
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from fact_sheet import Fact, build_fact_sheet, render_fact_sheet
from narrative_agents import DEFAULT_MODEL, VerifiedClaim, generate_recommendation, generate_risk, verify_and_correct
from verification_tools import ToolError, load_feature_frame, log, pick_demo_target

_AGE_LABEL = {"20": "20대", "30": "30대", "both": "20~30대"}
_CHARACTER_LABEL = {"foot": "유동인구 중심", "resident": "주거 배후", "worker": "직장 배후", "campus": "대학가"}
_PRIORITY_LABEL = {"survival": "생존 안정성", "cost": "예산 적합성", "growth": "성장 가능성"}


def conditions_text(budget: float, age: str, character: str, priority: str) -> str:
    return (
        f"보증금 {budget:,.0f}만원 이하 · 타깃 {_AGE_LABEL[age]} · "
        f"상권 성격: {_CHARACTER_LABEL[character]} · 우선순위: {_PRIORITY_LABEL[priority]}"
    )


def run(
    client: anthropic.Anthropic,
    district_name: str,
    business_label: str,
    facts: dict[str, Fact],
    conditions: str,
    model: str = DEFAULT_MODEL,
) -> dict[str, list[VerifiedClaim]]:
    """Fact Sheet -> (Recommendation, Risk) Agent -> Verification Agent."""
    rec_output = generate_recommendation(client, district_name, business_label, conditions, facts, model=model)
    risk_output = generate_risk(client, district_name, business_label, conditions, facts, model=model)

    return {
        "recommendation": verify_and_correct(client, rec_output.claims, facts, model=model),
        "risk": verify_and_correct(client, risk_output.claims, facts, model=model),
    }


def print_report(district_name: str, business_label: str, result: dict[str, list[VerifiedClaim]]) -> None:
    total = sum(len(v) for v in result.values())
    rejected = sum(1 for v in result.values() for c in v if c.corrected)
    dropped = sum(1 for v in result.values() for c in v if not c.verified)

    log(f"=== {district_name} × {business_label} ===")
    for label, key in (("추천 근거 (Recommendation Agent)", "recommendation"), ("반대 근거 (Risk Agent)", "risk")):
        print(f"\n[{label}]")
        for c in result[key]:
            if c.verified:
                mark = "✅ (정정됨)" if c.corrected else "✅"
                print(f"  {mark} {c.claim_text}")
            else:
                print(f"  ❌ 검증 실패 — 최종 응답에서 제외: \"{c.claim_text}\" ({c.verification_reason})")

    print(
        f"\n[검증 요약] 전체 {total}개 문장 · 1차 반려 후 정정 {rejected}개 "
        f"({0 if total == 0 else round(rejected / total * 100, 1)}%) · "
        f"최종 제외 {dropped}개"
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="골목 컴퍼스 전체 파이프라인 데모")
    p.add_argument("--csv", type=Path, default=None, help="district_features_debug.csv 경로")
    p.add_argument("--supabase", action="store_true", help="로컬 CSV 대신 Supabase에서 로드")
    p.add_argument("--district-code", default=None)
    p.add_argument("--business-code", default=None)
    p.add_argument("--budget", type=float, default=5000, help="보증금 예산(만원)")
    p.add_argument("--estimated-deposit", type=float, default=6200, help="추정 보증금(만원, 외부 추정치)")
    p.add_argument("--age", choices=["20", "30", "both"], default="both")
    p.add_argument("--character", choices=["foot", "resident", "worker", "campus"], default="foot")
    p.add_argument("--priority", choices=["survival", "cost", "growth"], default="survival")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true", help="Claude를 부르지 않고 Fact Sheet만 출력 (API 키 불필요)")
    return p.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    df = load_feature_frame(csv_path=args.csv, use_supabase=args.supabase)
    business_code, district_code, biz_rows = pick_demo_target(df, args.business_code, args.district_code)
    row = biz_rows[biz_rows["district_code"] == district_code]
    if row.empty:
        raise ToolError(f"상권_코드 {district_code} 는 업종 {business_code} 데이터에 없습니다.")

    district_name = row["district_name"].iloc[0]
    business_label = row["business_name"].iloc[0]
    conditions = conditions_text(args.budget, args.age, args.character, args.priority)

    facts = build_fact_sheet(
        df, district_code, business_code, budget=args.budget, estimated_deposit=args.estimated_deposit
    )
    log(f"Fact Sheet {len(facts)}개 생성 — {district_name} × {business_label}")

    if args.dry_run:
        print(render_fact_sheet(facts))
        log("--dry-run: Claude 호출 없이 종료")
        return

    client = anthropic.Anthropic()
    result = run(client, district_name, business_label, facts, conditions, model=args.model)
    print_report(district_name, business_label, result)


if __name__ == "__main__":
    try:
        main()
    except ToolError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)
    except anthropic.AuthenticationError:
        print("오류: ANTHROPIC_API_KEY가 없거나 잘못됐습니다. .env를 확인하세요.", file=sys.stderr)
        sys.exit(1)
    except anthropic.RateLimitError as exc:
        print(f"오류: Rate limit — {exc}", file=sys.stderr)
        sys.exit(1)
    except anthropic.APIConnectionError:
        print("오류: Anthropic API에 연결할 수 없습니다. 네트워크를 확인하세요.", file=sys.stderr)
        sys.exit(1)
    except anthropic.APIStatusError as exc:
        print(f"오류: API 오류 ({exc.status_code}) — {exc.message}", file=sys.stderr)
        sys.exit(1)
