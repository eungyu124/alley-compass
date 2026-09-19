#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
골목 컴퍼스 (Alley Compass) — PDF 리포트
PRD §19 Document 스택(WeasyPrint), F-15 구현

POST /report 가 만드는 산출물. Top-K 상권 + 각 상권의 추천/반대 근거를
한 장의 PDF로 만든다. main.py가 Recommendation/Risk/Verification Agent를
상권마다 호출해 만든 결과(section)를 넘겨주면, 이 파일은 그걸 HTML로
렌더링해 WeasyPrint로 PDF 변환만 한다 — 여기서 새로 근거를 만들거나
검증하지 않는다(그 로직은 narrative_agents.py 하나에만 있어야 한다).

macOS + Homebrew 노트
    WeasyPrint는 Pango/cairo/glib(libgobject)를 dlopen으로 찾는데, Apple
    Silicon Homebrew(/opt/homebrew)는 기본 라이브러리 탐색 경로에 없다.
    이 파일을 import하는 순간 dlopen이 실행되므로, weasyprint를 import하기
    전에 DYLD_LIBRARY_PATH를 넣어야 한다. Linux 서버에서는 Pango 등이 보통
    /usr/lib에 있어 필요 없다 — sys.platform 분기로 macOS에서만 적용한다.
"""

from __future__ import annotations

import os
import sys


def _patch_macos_library_path() -> None:
    if sys.platform != "darwin":
        return
    extra = "/opt/homebrew/lib:/usr/local/lib"
    current = os.environ.get("DYLD_LIBRARY_PATH", "")
    os.environ["DYLD_LIBRARY_PATH"] = f"{extra}:{current}" if current else extra


_patch_macos_library_path()

from datetime import datetime  # noqa: E402
from html import escape  # noqa: E402
from typing import Any  # noqa: E402

from weasyprint import HTML  # noqa: E402

AXIS_LABEL = {
    "demand": "수요",
    "competition": "경쟁 여유",
    "performance": "매출 추세",
    "access": "접근성",
    "stability": "폐업 안정성",
}

_CSS = """
@page { size: A4; margin: 20mm 16mm; @bottom-center { content: counter(page) " / " counter(pages); font-size: 9px; color: #77808C; } }
* { box-sizing: border-box; }
body { font-family: "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif; color: #1A2430; font-size: 11px; line-height: 1.5; }
h1 { font-size: 20px; margin: 0 0 4px; color: #0E3F6E; }
.meta { color: #77808C; font-size: 10px; margin-bottom: 4px; }
.conditions { background: #E4EDF5; color: #0E3F6E; border-radius: 6px; padding: 8px 12px; font-size: 10.5px; margin-bottom: 4px; }
.model-note { font-size: 9.5px; color: #B0801F; margin-bottom: 16px; }
.district { border: 1px solid #DBD8CD; border-radius: 8px; padding: 12px 14px; margin-bottom: 12px; page-break-inside: avoid; }
.dhead { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.rank { font-size: 16px; font-weight: 700; color: #1C5D99; }
.dname { font-size: 14px; font-weight: 700; }
.dcode { font-size: 9.5px; color: #77808C; font-family: monospace; }
.score { margin-left: auto; font-size: 16px; font-weight: 700; }
.axes { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 6px 0 10px; }
.axis { display: flex; align-items: center; gap: 4px; font-size: 9.5px; width: 30%; }
.al { width: 46px; color: #414D5C; }
.atrack { flex: 1; height: 5px; background: #E7E4D9; border-radius: 3px; overflow: hidden; }
.atrack i { display: block; height: 100%; background: #1C5D99; }
.av { width: 26px; text-align: right; color: #77808C; }
.cols { display: flex; gap: 16px; }
.col { flex: 1; }
.col h4 { font-size: 10.5px; margin: 0 0 4px; text-transform: uppercase; letter-spacing: .03em; }
.pro h4 { color: #1F5C3D; } .risk h4 { color: #8F4322; }
ul { margin: 0; padding-left: 14px; }
li { margin-bottom: 4px; }
.empty { color: #77808C; font-style: italic; }
footer.sources { font-size: 9px; color: #77808C; border-top: 1px solid #DBD8CD; padding-top: 8px; margin-top: 16px; }
"""


def _axes_html(breakdown: dict[str, Any]) -> str:
    rows = []
    for key, label in AXIS_LABEL.items():
        v = breakdown.get(key)
        width = 0 if v is None else max(0, min(100, v))
        val = "—" if v is None else f"{v:.0f}"
        rows.append(
            f'<div class="axis"><span class="al">{escape(label)}</span>'
            f'<span class="atrack"><i style="width:{width:.0f}%"></i></span>'
            f'<span class="av">{val}</span></div>'
        )
    return "".join(rows)


def _claims_html(claims: list[dict[str, Any]], empty_note: str) -> str:
    if not claims:
        return f'<p class="empty">{escape(empty_note)}</p>'
    items = []
    for c in claims:
        suffix = " (검증 후 정정됨)" if c.get("corrected") else ""
        items.append(f"<li>{escape(c['text'])}{escape(suffix)}</li>")
    return f"<ul>{''.join(items)}</ul>"


def _district_html(section: dict[str, Any]) -> str:
    if section.get("agent_error"):
        body = f'<p class="empty">추천/반대 근거 생성 실패: {escape(section["agent_error"])}</p>'
    else:
        body = (
            '<div class="cols">'
            '<div class="col pro"><h4>추천 근거</h4>'
            + _claims_html(section["recommendation"], "검증을 통과한 추천 근거가 없습니다.")
            + '</div><div class="col risk"><h4>반대 근거</h4>'
            + _claims_html(section["risk"], "검증을 통과한 반대 근거가 없습니다.")
            + "</div></div>"
        )
    return f"""
    <div class="district">
      <div class="dhead">
        <span class="rank">{section['rank']}위</span>
        <span class="dname">{escape(section['district_name'])}</span>
        <span class="dcode">상권_코드 {escape(section['district_code'])}</span>
        <span class="score">{section['final_score']:.0f}점</span>
      </div>
      <div class="axes">{_axes_html(section['breakdown'])}</div>
      {body}
    </div>
    """


def render_report_html(
    *,
    business_name: str,
    conditions_text: str,
    as_of: str,
    model_version: str,
    sections: list[dict[str, Any]],
) -> str:
    model_note = (
        f"생존 안정성 Score는 LightGBM 학습 전이라 원본 feature 기반 임시 계산값입니다 "
        f"(model_version: {model_version})."
        if model_version == "heuristic-v0"
        else f"생존 안정성 Score는 LightGBM(model_version: {model_version})이 산출한 값입니다."
    )
    body = "".join(_district_html(s) for s in sections)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>
<h1>골목 컴퍼스 — 상권 추천 리포트</h1>
<div class="meta">{escape(business_name)} · 생성 {generated_at} · 데이터 기준시점 {escape(as_of)}</div>
<div class="conditions">{escape(conditions_text)}</div>
<div class="model-note">{escape(model_note)}</div>
{body}
<footer class="sources">
  출처: 서울 열린데이터광장(data.seoul.go.kr) · 우리마을가게 상권분석서비스 · 제공 서울신용보증재단 · 공공누리 제1유형.
  추천/반대 근거는 Recommendation/Risk Agent(Claude)가 생성하고 Verification Agent가 원본 데이터와
  대조해 검증을 통과한 문장만 실었습니다. Score는 개별 점포의 생존확률이 아니라 상권×업종 단위의
  폐업위험/생존 안정성 지표입니다.
</footer>
</body></html>"""


def build_report_pdf(**kwargs: Any) -> bytes:
    html = render_report_html(**kwargs)
    return HTML(string=html).write_pdf()
