import { ArrowUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { RichParts } from "@/lib/rich";
import { Button, Card, CardBody, CardHeader, CardTitle, Input } from "@/components/ui";

import { Rich } from "./Rich";

/* ──────────────────────────────────────────────────────────────
 * 대화형 재탐색 (F-12).
 *
 * 조건을 자연어 문장으로 입력받는다 — 예: "양식 창업하고 싶은데 예산 3천
 * 정도로 어디가 좋을까". 문장은 backend /parse-condition(Claude 구조화
 * 출력)이 조건으로 바꾸고, 이전 조건을 같이 보내므로 이번 문장에서
 * 언급 안 한 값은 그대로 유지된다 — 매번 처음부터 다시 말할 필요가 없다.
 *
 * 자주 쓰는 조건은 칩으로도 남겨둔다 — 문장을 치기 귀찮을 때 바로 누르면
 * 되고, 새 사용자에게는 "이렇게 물어보면 되는구나"의 예시가 된다. "다른
 * 업종은?" 칩은 실제로 수집된 업종이 2개 이상일 때만 만든다. 하나뿐인데
 * 칩을 보여주면 없는 업종을 부르다 404가 난다 — 지어낸 선택지를 보여주지
 * 않는다는 원칙이 UI 쪽에도 그대로 적용된다.
 *
 * 로그는 aria-live 로 읽어준다. 조건을 바꾸면 화면 다른 쪽(순위)이 통째로
 * 바뀌는데, 스크린리더 사용자는 그 변화를 알 방법이 없기 때문이다.
 * ────────────────────────────────────────────────────────────── */

export type QuickAskAction = "otherBiz" | "budget3000" | "age20" | "resident" | "reset";

export interface Message {
  id: number;
  from: "user" | "bot";
  parts: RichParts;
}

export interface AskPanelProps {
  messages: readonly Message[];
  onAsk: (action: QuickAskAction) => void;
  /** 자연어 문장 제출. Claude 호출 1회라 추천/리포트 생성보다 훨씬 저렴하다. */
  onParse: (message: string) => void;
  /** 수집된 업종 수. 2개 이상일 때만 업종 전환 칩을 만든다. */
  businessTypeCount: number;
  busy?: boolean;
}

export function AskPanel({
  messages,
  onAsk,
  onParse,
  businessTypeCount,
  busy = false,
}: AskPanelProps) {
  const logRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const chips: Array<{ action: QuickAskAction; label: string }> = [
    ...(businessTypeCount > 1
      ? ([{ action: "otherBiz", label: "다른 업종으로 보면?" }] as const)
      : []),
    { action: "budget3000", label: "예산을 3,000만원으로" },
    { action: "age20", label: "20대 유동인구만" },
    { action: "resident", label: "조용한 주거 배후로" },
    { action: "reset", label: "처음 조건으로" },
  ];

  const submit = () => {
    const message = draft.trim();
    if (!message || busy) return;
    onParse(message);
    setDraft("");
  };

  return (
    <Card>
      <CardHeader divided>
        <CardTitle as="h3" className="text-sm">
          대화형 재탐색
        </CardTitle>
      </CardHeader>

      <CardBody className="pt-3">
        <div
          ref={logRef}
          role="log"
          aria-live="polite"
          aria-label="분석 대화 기록"
          className="scroll-slim flex max-h-72 flex-col gap-2.5 overflow-y-auto pr-1"
        >
          {messages.length === 0 ? (
            <p className="text-xs text-fg-subtle">
              "양식 창업하고 싶은데 예산 3천 정도로 어디가 좋을까"처럼 편하게 물어보세요.
            </p>
          ) : null}

          {messages.map((m) => (
            <div
              key={m.id}
              className={
                m.from === "user"
                  ? "max-w-[85%] self-end rounded-xl rounded-br-sm bg-accent-subtle px-3 py-2"
                  : "max-w-[92%] rounded-xl rounded-bl-sm bg-surface-sunken px-3 py-2"
              }
            >
              <p className="mb-0.5 font-mono text-2xs uppercase tracking-wider text-fg-subtle">
                {m.from === "user" ? "나" : "골목 컴퍼스"}
              </p>
              <p className="text-xs leading-relaxed text-fg-body">
                <Rich parts={m.parts} />
              </p>
            </div>
          ))}
        </div>

        <div className="mt-3 flex gap-1.5 border-t border-border-subtle pt-3">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) submit();
            }}
            placeholder="조건을 문장으로 입력하세요"
            disabled={busy}
            wrapperClassName="flex-1"
            aria-label="조건을 문장으로 입력"
          />
          <Button
            variant="solid"
            size="md"
            disabled={busy || !draft.trim()}
            onClick={submit}
            aria-label="전송"
          >
            <ArrowUp aria-hidden="true" className="size-4" />
          </Button>
        </div>

        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {chips.map((chip) => (
            <Button
              key={chip.action}
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => onAsk(chip.action)}
            >
              {chip.label}
            </Button>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}
