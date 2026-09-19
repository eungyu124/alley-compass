import { Loader2 } from "lucide-react";

import { AGE_LABEL, CHARACTER_LABEL, PRIORITY_LABEL } from "@/data/businessTypes";
import { fmt } from "@/lib/format";
import type { RankResponse } from "@/types/api";
import type { Conditions } from "@/types/domain";

/* 결과 머리말 — "지금 무엇을, 무슨 기준으로 보고 있는가".
 * 조건을 바꾸면 이 줄이 먼저 바뀌므로 재계산이 일어났다는 신호도 겸한다.
 *
 * ConditionBar가 없어진 뒤로는(자연어 입력으로 대체) 지금 조건이 화면
 * 어디에도 안 보일 수 있어서, 여기에 현재 조건을 요약 pill로 보여준다.
 * 값을 바꾸는 컨트롤은 아니고 "지금 뭘 기준으로 보고 있는지" 확인용이다. */

export interface ResultSummaryProps {
  meta: RankResponse | null;
  loading: boolean;
  conditions: Conditions;
}

export function ResultSummary({ meta, loading, conditions }: ResultSummaryProps) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
      <div className="min-w-0">
        <h2 className="text-xl font-semibold text-fg">
          내 조건에서 살아남을 가능성이 높은 골목상권
        </h2>

        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-muted">
          {meta ? (
            <>
              <span className="font-medium text-fg-body">{meta.business_name}</span>
              <span aria-hidden="true">·</span>
              <span>
                서울 골목상권{" "}
                <span className="font-mono tabular-nums text-fg">{fmt(meta.n_candidates)}곳</span>{" "}
                비교
              </span>
              <span aria-hidden="true">·</span>
              <span>
                상위 <span className="font-mono tabular-nums text-fg">{meta.results.length}곳</span>{" "}
                표시
              </span>
            </>
          ) : (
            <span>조건에 맞는 상권을 찾는 중…</span>
          )}

          {loading ? (
            <span
              className="inline-flex items-center gap-1 text-accent-text"
              role="status"
              aria-label="다시 찾는 중"
            >
              <Loader2 aria-hidden="true" className="size-3 animate-spin" />
              다시 찾는 중
            </span>
          ) : null}
        </p>

        <p className="mt-2 flex flex-wrap items-center gap-1.5 text-2xs text-fg-subtle">
          {[
            conditions.budget ? `보증금 ${fmt(conditions.budget)}만원 이하` : null,
            AGE_LABEL[conditions.age],
            CHARACTER_LABEL[conditions.character],
            PRIORITY_LABEL[conditions.priority],
          ]
            .filter((v): v is string => Boolean(v))
            .map((label) => (
              <span
                key={label}
                className="rounded-full border border-border-subtle bg-surface-sunken px-2 py-0.5"
              >
                {label}
              </span>
            ))}
        </p>
      </div>
    </div>
  );
}
