import type { AgeTarget, Conditions, MarketCharacter, Priority } from "@/types/domain";

/* ──────────────────────────────────────────────────────────────
 * "조건을 한 번이라도 입력해봤다"의 저장소.
 *
 * 별도의 onboarded 플래그를 두지 않는다 — 저장된 조건이 있으면 그 자체가
 * "이미 온보딩을 끝냈다"는 뜻이고, 없으면 아직이라는 뜻이다. 플래그와
 * 조건을 따로 두면 플래그만 있고 조건이 없는(또는 그 반대) 상태가 생길 수
 * 있는데, 그러면 축소 화면으로 바로 들어갔는데 정작 보여줄 조건이 없는
 * 상황이 된다.
 *
 * 사용자별로 키를 나눈다 — 로그인 필수 서비스라 브라우저 하나를 여러
 * 계정이 같이 쓸 수 있고, 그러면 이전 사용자의 조건이 새 사용자에게
 * 그대로 보이면 안 되기 때문이다.
 * ────────────────────────────────────────────────────────────── */

const KEY_PREFIX = "alleycompass:conditions:v1:";

const AGE_VALUES: readonly AgeTarget[] = ["20", "30", "both"];
const CHARACTER_VALUES: readonly MarketCharacter[] = ["foot", "resident", "worker", "campus"];
const PRIORITY_VALUES: readonly Priority[] = ["survival", "cost", "growth"];

function isConditions(value: unknown): value is Conditions {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.biz === "string" &&
    v.biz.length > 0 &&
    typeof v.budget === "number" &&
    Number.isFinite(v.budget) &&
    AGE_VALUES.includes(v.age as AgeTarget) &&
    CHARACTER_VALUES.includes(v.character as MarketCharacter) &&
    PRIORITY_VALUES.includes(v.priority as Priority)
  );
}

/** 저장된 조건. 없거나 손상됐으면(다른 버전, 수동 편집 등) null — 이때는 다시 온보딩부터 시작한다. */
export function loadConditions(userId: string): Conditions | null {
  try {
    const raw = localStorage.getItem(KEY_PREFIX + userId);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isConditions(parsed) ? parsed : null;
  } catch {
    // 프라이빗 브라우징 등에서 localStorage 접근이 막혀 있을 수 있다 —
    // 이때는 그냥 온보딩부터 다시 시작한다(핵심 기능에는 지장 없음).
    return null;
  }
}

export function saveConditions(userId: string, conditions: Conditions): void {
  try {
    localStorage.setItem(KEY_PREFIX + userId, JSON.stringify(conditions));
  } catch {
    // 저장 실패해도 이번 세션 사용에는 지장이 없다 — 다음 로그인 때
    // 온보딩 화면을 한 번 더 보여주는 정도로 그친다.
  }
}
