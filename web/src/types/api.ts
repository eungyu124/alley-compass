/* ──────────────────────────────────────────────────────────────
 * 백엔드 응답 타입 — backend/schemas.py 와 1:1 대응.
 *
 * 호출은 전부 lib/api.ts 를 지난다. 스키마가 바뀌면 이 파일과
 * backend/schemas.py 를 함께 고친다.
 *
 * 장기적으로는 FastAPI 의 OpenAPI 스키마에서 생성하는 편이 안전하다.
 * ────────────────────────────────────────────────────────────── */

import type { AgeTarget, MarketCharacter, Priority } from "./domain";

export interface RankRequest {
  business_code: string;
  /** 보증금 예산(만원). 현재 랭킹 계산에는 반영되지 않는다(임대료 데이터 부재). */
  budget?: number | null;
  age: AgeTarget;
  character: MarketCharacter;
  priority: Priority;
  top_k: number;
}

/** 5개 축의 점수. 데이터가 없어 제외된 축은 null 이다. */
export interface ScoreBreakdown {
  demand: number | null;
  competition: number | null;
  performance: number | null;
  access: number | null;
  stability: number | null;
}

export interface DistrictScore {
  rank: number;
  district_code: string;
  district_name: string;
  stability_score: number;
  target_fit_score: number;
  final_score: number;
  score_breakdown: ScoreBreakdown;
}

export interface RankResponse {
  run_id: string | null;
  business_code: string;
  business_name: string;
  /** 사용된 기준시점 (YYYY-MM-DD) */
  as_of: string;
  n_candidates: number;
  /** "heuristic-v0" 이면 아직 LightGBM 이 아니다 */
  model_version: string;
  /** 데이터 부족으로 이번 랭킹에서 빠진 요소 안내 */
  warnings: string[];
  results: DistrictScore[];
}

export interface AgentRequest {
  business_code: string;
  budget?: number | null;
  /** 외부 추정 보증금(만원). budget 과 함께 주면 예산 여유 Fact 가 생긴다. */
  estimated_deposit?: number | null;
  age: AgeTarget;
  character: MarketCharacter;
  priority: Priority;
  model?: string | null;
}

/** Agent 가 만든 문장 하나와 그 검증 결과 */
export interface VerifiedClaim {
  claim_text: string;
  fact_key: string;
  claimed_value: number;
  actual_value: number | null;
  tolerance: number | null;
  /** false 면 사용자에게 노출하지 않는다 (PRD §18) */
  verified: boolean;
  verification_tool: string;
  verification_reason: string;
  /** 1차 반려 후 재작성된 문장인가 */
  corrected: boolean;
}

export interface FactOut {
  label: string;
  value: number;
  unit: string;
}

export interface AgentResponse {
  district_code: string;
  district_name: string;
  business_label: string;
  /** Agent 에게 건넨 Fact 목록 — 검증 로그를 화면에 그릴 때 함께 쓴다 */
  facts: Record<string, FactOut>;
  recommendation: VerifiedClaim[];
  risk: VerifiedClaim[];
}

/* ── 상세 지표 (GET /districts/{code}/detail) ─────────────────────
 * backend/detail.py 가 결정론적으로 집계한다. Claude 를 부르지 않으므로
 * 상권을 열 때마다 불러도 과금되지 않는다. */

export interface DiagnosticRowOut {
  label: string;
  value: string;
  /** 서울 골목상권 내 상위 %. 비교 대상이 없으면 null. */
  top: number | null;
  unverified: boolean;
}

export type DiagnosticKey = "customers" | "competition" | "environment" | "cost";

export interface DiagnosticAreaOut {
  key: DiagnosticKey;
  label: string;
  caption: string;
  /** false 면 score/top_pct 가 null — 없는 데이터에 점수를 만들지 않는다 */
  available: boolean;
  score: number | null;
  top_pct: number | null;
  rows: DiagnosticRowOut[];
  note: string | null;
}

export interface SeriesPoint {
  label: string;
  value: number;
}

export interface Series {
  available: boolean;
  /** available=false 인 이유. 화면에 그대로 보여준다. */
  reason: string | null;
  points: SeriesPoint[];
}

export interface CompetitionChartOut {
  district_value: number;
  city_avg: number;
  store_count: number;
  n_districts: number;
  ratio_to_avg: number | null;
}

export interface DetailResponse {
  district_code: string;
  district_name: string;
  business_code: string;
  business_name: string;
  as_of: string;
  /** 실제로 수집된 분기 수. 1이면 추세 계열이 전부 비어 있다. */
  quarters_collected: number;
  diagnostics: DiagnosticAreaOut[];
  series: {
    hourly: Series;
    sales: Series;
    closure: Series;
    competition: CompetitionChartOut | null;
  };
  metrics_available: string[];
}

export interface BusinessTypeOut {
  business_code: string;
  business_name: string;
}

/* ── 자연어 조건 파서 (POST /parse-condition) ────────────────────
 * ConditionBar 를 대체하는 대화형 입력. previous 를 같이 보내면
 * 문장에서 언급 안 한 필드는 그대로 유지된다. */

export interface ConditionState {
  business_code: string | null;
  budget: number | null;
  age: AgeTarget;
  character: MarketCharacter;
  priority: Priority;
}

export interface ParseConditionRequest {
  message: string;
  previous: ConditionState;
}

export interface ParseConditionResponse {
  business_code: string | null;
  /** business_code 를 실제 업종명으로 바꾼 값. 참고용. */
  business_name: string | null;
  /** 목록에 없는 업종을 말했을 때 그 이름. 있으면 안내 문구를 띄운다. */
  business_not_found: string | null;
  budget: number | null;
  age: AgeTarget;
  character: MarketCharacter;
  priority: Priority;
}
