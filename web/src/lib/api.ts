import type {
  AgentResponse,
  BusinessTypeOut,
  DetailResponse,
  ParseConditionRequest,
  ParseConditionResponse,
  RankRequest,
  RankResponse,
} from "@/types/api";
import type { Conditions } from "@/types/domain";

import { getAccessToken } from "./auth";

/* ──────────────────────────────────────────────────────────────
 * backend/ FastAPI 클라이언트 — 백엔드 호출의 유일한 접점.
 *
 * 프론트는 Supabase 를 직접 읽지 않는다. Claude 호출(과금)이 필요한 순간부터
 * API 키를 프론트 번들에 둘 수 없어 결국 FastAPI 를 거쳐야 하고, 랭킹 로직도
 * backend/scoring.py 한 곳에만 있어야 화면 숫자와 verification_tools.py 의
 * 판정이 어긋나지 않는다. 그래서 무료 조회(랭킹·상세)까지 전부 여기를 지난다.
 *
 * 기본 주소는 로컬 개발 서버. 배포 시 .env.local 에 VITE_API_BASE_URL 을 둔다.
 * ────────────────────────────────────────────────────────────── */

export const BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"
).replace(/\/$/, "");

/** 백엔드가 준 detail 문구를 그대로 사용자에게 보여주기 위한 에러 타입 */
export class ApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }

  /** 세션이 끊겼다는 뜻. 화면은 로그인으로 돌려보낸다. */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 토큰은 캐시하지 않고 매번 가져온다 — 라이브러리가 백그라운드에서 갱신하므로
  // 들고 다니면 만료된 값을 보내게 된다.
  const token = await getAccessToken();
  let res: Response;

  try {
    res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(
      `백엔드(${BASE_URL})에 연결할 수 없습니다. uvicorn 이 켜져 있는지 확인하세요.`,
    );
  }

  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new ApiError(body.detail || `API 오류 (HTTP ${res.status})`, res.status);
  }

  return (await res.json()) as T;
}

/** 실제로 수집된 업종만 돌아온다. 1개뿐이면 데이터가 그만큼이라는 뜻이다. */
export function fetchBusinessTypes(): Promise<BusinessTypeOut[]> {
  return request<BusinessTypeOut[]>("/business-types");
}

/** PRD §16 개인화 Ranking. */
export function fetchRank(
  conditions: Conditions,
  topK = 20,
): Promise<RankResponse> {
  const body: RankRequest = {
    business_code: conditions.biz,
    budget: conditions.budget,
    age: conditions.age,
    character: conditions.character,
    priority: conditions.priority,
    top_k: topK,
  };

  return request<RankResponse>("/rank", { method: "POST", body: JSON.stringify(body) });
}

/**
 * 상권 진단 4영역 + 시계열. 전부 Pandas 집계라 과금이 없다 —
 * 상권을 열자마자 바로 불러도 된다.
 */
export function fetchDetail(
  districtCode: string,
  businessCode: string,
): Promise<DetailResponse> {
  const query = new URLSearchParams({ business_code: businessCode });
  return request<DetailResponse>(
    `/districts/${encodeURIComponent(districtCode)}/detail?${query}`,
  );
}

/**
 * PRD §10 Recommendation / Risk / Verification.
 *
 * ⚠️ Claude 를 최소 4번 부른다(추천 1 + 리스크 1 + 검증 2). 15~20초 걸리고
 * 호출마다 과금되므로 자동으로 부르지 않는다 — 사용자가 버튼을 눌러야 부른다.
 */
export function fetchAgents(
  districtCode: string,
  conditions: Conditions,
): Promise<AgentResponse> {
  return request<AgentResponse>(`/districts/${encodeURIComponent(districtCode)}/agents`, {
    method: "POST",
    body: JSON.stringify({
      business_code: conditions.biz,
      budget: conditions.budget,
      age: conditions.age,
      character: conditions.character,
      priority: conditions.priority,
    }),
  });
}

/**
 * 자연어 조건 파서 — ConditionBar 를 대체하는 대화형 입력.
 * conditions 를 previous 로 함께 보내면 이번 문장에서 언급 안 한 필드는
 * 그대로 유지된다(매번 새로 묻지 않는다). 텍스트 한두 문장짜리 호출이라
 * 추천/리포트 생성보다 훨씬 저렴하다.
 */
export function fetchParseCondition(
  message: string,
  conditions: Conditions,
): Promise<ParseConditionResponse> {
  const body: ParseConditionRequest = {
    message,
    previous: {
      business_code: conditions.biz || null,
      budget: conditions.budget,
      age: conditions.age,
      character: conditions.character,
      priority: conditions.priority,
    },
  };

  return request<ParseConditionResponse>("/parse-condition", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
