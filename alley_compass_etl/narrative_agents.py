#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — Recommendation / Risk / Verification Agent
PRD §9~§10 구현

역할 분리 (PRD §1, §9)
    Data/Prediction Layer(Pandas·LightGBM)가 계산한 숫자를,
    Claude는 "설명"만 한다 — 새 숫자를 계산하지 않는다.
    이 파일은 Claude를 부르는 유일한 곳이다.

    ① generate_recommendation() — Recommendation Agent
       fact_sheet.build_fact_sheet()가 만든 Fact 중 "추천 근거"로
       쓸 만한 것을 골라 자연어 문장으로 바꾼다.

    ② generate_risk()           — Risk Agent
       같은 Fact 목록을 독립적으로 다시 보고, 추천 결론을 따라가지
       않고 리스크·취약점으로 읽히는 근거를 적극적으로 찾는다
       (PRD §10.2).

    ③ verify_and_correct()      — Verification Agent
       Claude가 문장에 실제로 적은 수치(stated_value)를
       verification_tools.assertion_validator()로 Fact의 실제 값과
       대조한다. 불일치하면 정확한 값을 알려주고 딱 한 번 재작성을
       요청한 뒤 다시 검증한다 — PRD §10.3의 "1차 반려 -> 정정" 그대로.
       두 번째도 실패하면 그 문장은 최종 결과에서 제외한다
       (PRD §18: 검증 실패 시 최종 사용자에게 노출하지 않는다).

여기서 "검증"은 LLM의 판단이 아니라 결정론적 assertion
(verification_tools.assertion_validator)이다 — AI가 설명하고,
코드가 증명한다 (PRD §10.3 핵심 원칙).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from fact_sheet import Fact, render_fact_sheet
from verification_tools import assertion_validator

DEFAULT_MODEL = "claude-sonnet-5"

Stance = Literal["recommend", "risk"]


class ClaimOut(BaseModel):
    fact_key: str = Field(description="이 문장의 근거로 쓴 Fact Sheet의 key. 반드시 제공된 목록에 있는 값 그대로.")
    stated_value: float = Field(description="문장에 실제로 적은 수치. Fact의 값을 그대로 쓰거나 소수 첫째 자리까지 반올림.")
    text: str = Field(description="근거 문장 하나. stated_value가 자연스러운 한국어 문장 안에 포함되어야 함.")


class AgentOutput(BaseModel):
    claims: list[ClaimOut] = Field(min_length=2, max_length=4)


_COMMON_RULES = """\
당신은 '골목 컴퍼스' 서비스의 {role}입니다.

아래는 한 상권×업종 조합에 대해 이미 계산이 끝난 사실(Fact) 목록입니다.
이 값들은 서울 열린데이터광장 원본 데이터를 Pandas로 집계한 결과이며,
전부 사실입니다.

규칙:
1. 새 숫자를 계산하거나 추정하지 마세요. 목록에 없는 수치를 지어내지 마세요.
2. 각 문장은 목록에 있는 Fact 중 정확히 하나를 근거로 삼아야 합니다.
3. stated_value에는 그 Fact의 값을 소수 첫째 자리까지 반올림해 그대로 적으세요.
4. 문장은 예비 창업자가 이해하기 쉬운 자연스러운 한국어로 쓰세요.
5. 같은 Fact를 두 문장에서 중복해서 쓰지 마세요.
"""

_SYSTEM_RECOMMEND = _COMMON_RULES.format(role="Recommendation Agent") + """
이 상권이 이 업종에 적합하다는 근거로 쓸 수 있는 Fact를 우선 골라
2~3개의 추천 문장을 작성하세요.
"""

_SYSTEM_RISK = _COMMON_RULES.format(role="Risk Agent") + """
Recommendation Agent와 독립적으로 판단하세요. 그 결론을 따라가지 말고,
과포화·매출 부진·폐업 증가·예산 초과처럼 이 후보의 취약점으로 읽히는
Fact를 적극적으로 찾아 2~3개의 반대 근거(리스크) 문장을 작성하세요.
"""

_CORRECTION_SYSTEM = """\
당신은 '골목 컴퍼스'의 Recommendation/Risk Agent입니다.
방금 작성한 문장의 수치가 실제 값과 달라 Verification Agent가 반려했습니다.
같은 Fact(fact_key는 그대로)를 근거로, 이번에는 제공된 정확한 값을 사용해
문장을 다시 작성하세요. 다른 내용은 바꾸지 마세요.
"""


def _user_prompt(district_name: str, business_label: str, conditions_text: str, facts: dict[str, Fact]) -> str:
    return (
        f"상권: {district_name}\n"
        f"업종: {business_label}\n"
        f"사용자 조건: {conditions_text}\n\n"
        f"[사용 가능한 Fact 목록]\n{render_fact_sheet(facts)}"
    )


def _generate(
    client: anthropic.Anthropic,
    stance: Stance,
    district_name: str,
    business_label: str,
    conditions_text: str,
    facts: dict[str, Fact],
    model: str = DEFAULT_MODEL,
) -> AgentOutput:
    system = _SYSTEM_RECOMMEND if stance == "recommend" else _SYSTEM_RISK
    response = client.messages.parse(
        model=model,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": _user_prompt(district_name, business_label, conditions_text, facts)}],
        output_format=AgentOutput,
    )
    return response.parsed_output


def generate_recommendation(
    client: anthropic.Anthropic,
    district_name: str,
    business_label: str,
    conditions_text: str,
    facts: dict[str, Fact],
    model: str = DEFAULT_MODEL,
) -> AgentOutput:
    """Recommendation Agent. (PRD §10.1)"""
    return _generate(client, "recommend", district_name, business_label, conditions_text, facts, model)


def generate_risk(
    client: anthropic.Anthropic,
    district_name: str,
    business_label: str,
    conditions_text: str,
    facts: dict[str, Fact],
    model: str = DEFAULT_MODEL,
) -> AgentOutput:
    """Risk Agent. Recommendation Agent와 별도로 호출해 독립성을 보장한다. (PRD §10.2)"""
    return _generate(client, "risk", district_name, business_label, conditions_text, facts, model)


@dataclass
class VerifiedClaim:
    claim_text: str
    fact_key: str
    claimed_value: float
    actual_value: float | None
    tolerance: float | None
    verified: bool
    verification_tool: str
    verification_reason: str
    corrected: bool


def _verify_one(claim: ClaimOut, facts: dict[str, Fact]) -> tuple[bool, dict]:
    fact = facts.get(claim.fact_key)
    if fact is None:
        return False, {
            "actual_value": None,
            "tolerance": None,
            "verification_reason": f"존재하지 않는 fact_key 인용: '{claim.fact_key}'",
        }
    result = assertion_validator(claimed_value=claim.stated_value, actual_value=fact.value, tolerance=fact.tolerance)
    return result["verified"], {
        "actual_value": fact.value,
        "tolerance": fact.tolerance,
        "verification_reason": result["verification_reason"],
    }


def verify_and_correct(
    client: anthropic.Anthropic,
    claims: list[ClaimOut],
    facts: dict[str, Fact],
    model: str = DEFAULT_MODEL,
) -> list[VerifiedClaim]:
    """각 Claim을 Assertion Validator로 검증하고, 실패하면 정확한 값을 알려주고
    딱 한 번 재작성을 요청한 뒤 다시 검증한다. (PRD §10.3)

    최종 verified=False인 문장은 호출자가 사용자에게 노출하지 않아야 한다
    (PRD §18 Hallucination Control).
    """
    out: list[VerifiedClaim] = []

    for claim in claims:
        verified, info = _verify_one(claim, facts)
        corrected = False

        if not verified and claim.fact_key in facts:
            fact = facts[claim.fact_key]
            correction_prompt = (
                f"원래 문장: \"{claim.text}\"\n"
                f"주장한 값: {claim.stated_value}{fact.unit}\n"
                f"실제 값({fact.label}): {fact.value}{fact.unit}\n"
                f"fact_key={fact.key} 를 유지한 채, 실제 값을 반영해 문장을 다시 작성하세요."
            )
            response = client.messages.parse(
                model=model,
                max_tokens=500,
                system=_CORRECTION_SYSTEM,
                messages=[{"role": "user", "content": correction_prompt}],
                output_format=ClaimOut,
            )
            claim = response.parsed_output
            corrected = True
            verified, info = _verify_one(claim, facts)

        out.append(
            VerifiedClaim(
                claim_text=claim.text,
                fact_key=claim.fact_key,
                claimed_value=claim.stated_value,
                actual_value=info["actual_value"],
                tolerance=info["tolerance"],
                verified=verified,
                verification_tool="Assertion Validator",
                verification_reason=info["verification_reason"],
                corrected=corrected,
            )
        )

    return out
