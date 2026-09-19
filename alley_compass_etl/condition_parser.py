#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — 조건 파서
PRD F-12 "대화형 재탐색"의 자연어 입력 버전.

    사용자 자유 문장 (+ 직전 조건)
        -> Claude가 구조화된 조건(ParsedCondition)으로 변환
        -> 언급 안 한 필드는 직전 조건을 그대로 유지한다 (진짜 "대화")
        -> business_code는 실제로 수집된 업종 목록 중에서만 고르게 하고,
           Claude가 뭐라 답하든 파이썬이 그 목록에 있는지 다시 확인한다
           (fact_sheet.py와 같은 원칙 — Claude는 설명하고, 코드가 증명한다)

이 파일은 narrative_agents.py와 다른 목적이다 — 근거 문장을 만드는 게
아니라 사용자의 의도를 구조화된 조건으로 바꿀 뿐이라, 검증할 "수치 주장"이
없다. 그래서 verify_and_correct() 같은 재작성 루프도 필요 없고, 업종
코드 하나만 목록 대조로 확인하면 끝난다.
"""

from __future__ import annotations

from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-sonnet-5"

Age = Literal["20", "30", "both"]
Character = Literal["foot", "resident", "worker", "campus"]
Priority = Literal["survival", "cost", "growth"]


class ParsedCondition(BaseModel):
    business_code: Optional[str] = Field(
        description="아래 [사용 가능한 업종 목록]의 code 중 하나. 문장에서 언급 안 했으면 "
        "[현재 조건]의 business_code를 그대로 반환. 목록에 없는 업종을 언급했으면 null."
    )
    business_not_found: Optional[str] = Field(
        default=None,
        description="사용자가 언급했지만 목록에 없는 업종명. 없으면 null.",
    )
    budget: Optional[float] = Field(
        description="보증금 예산(만원 단위 숫자만, 예: '3천만원'->3000). 언급 안 했으면 "
        "[현재 조건]의 budget을 그대로 반환."
    )
    age: Age = Field(description="언급 안 했으면 [현재 조건]의 값을 그대로.")
    character: Character = Field(description="언급 안 했으면 [현재 조건]의 값을 그대로.")
    priority: Priority = Field(description="언급 안 했으면 [현재 조건]의 값을 그대로.")


_SYSTEM = """\
당신은 '골목 컴퍼스'의 조건 파서입니다. 사용자의 자유 문장에서 창업 입지 탐색
조건을 추출합니다. 새 조건을 계산하거나 상권을 추천하지 않습니다 — 오직
문장을 구조화된 필드로 바꾸는 역할만 합니다.

규칙:
1. business_code는 [사용 가능한 업종 목록]에 있는 code만 쓸 수 있습니다.
   목록에 없는 업종(예: "양식", "네일샵")을 언급했다면 business_code는 null로
   하고, business_not_found에 사용자가 말한 이름을 그대로 적으세요. 절대
   목록에 없는 code를 지어내지 마세요.
2. 이건 대화가 이어지는 상황입니다. 문장에 언급되지 않은 필드는 [현재 조건]의
   값을 그대로 반환하세요 — 언급 안 한 걸 임의로 바꾸면 안 됩니다.
3. 예산은 "만원" 단위 숫자만 반환합니다.
4. [현재 조건]이 비어 있으면(첫 대화) 언급 안 된 age/character/priority는
   각각 both/foot/survival을 기본값으로 쓰세요.
"""


def parse_condition(
    client: anthropic.Anthropic,
    message: str,
    previous: dict,
    available_businesses: list[dict],
    model: str = DEFAULT_MODEL,
) -> ParsedCondition:
    """자유 문장 + 직전 조건 -> 구조화된 조건. business_code는 실제 목록으로 재검증한다."""
    biz_list = "\n".join(
        f"- {b['business_code']}: {b['business_name']}" for b in available_businesses
    )
    user_prompt = (
        f"[사용 가능한 업종 목록]\n{biz_list}\n\n"
        f"[현재 조건]\n{previous}\n\n"
        f"[사용자 문장]\n{message}"
    )

    response = client.messages.parse(
        model=model,
        max_tokens=500,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
        output_format=ParsedCondition,
    )
    result = response.parsed_output

    # Claude가 뭐라 답하든, 실제 목록에 있는 code인지 파이썬이 다시 확인한다.
    valid_codes = {b["business_code"] for b in available_businesses}
    if result.business_code is not None and result.business_code not in valid_codes:
        result = result.model_copy(
            update={
                "business_not_found": result.business_not_found or result.business_code,
                "business_code": None,
            }
        )
    return result
