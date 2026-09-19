import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import type { RichParts } from "@/lib/rich";

import { Rich } from "./Rich";

/* AskPanel(축소된 대화형 재탐색)과 OnboardingChat(첫 진입 풀스크린)이
 * 같은 대화 로그 모양을 쓴다 — 메시지 배열 하나로 두 군데를 그린다. */

export interface Message {
  id: number;
  from: "user" | "bot";
  parts: RichParts;
}

export interface ChatLogProps {
  messages: readonly Message[];
  /** 메시지가 아직 없을 때 보여줄 안내문 */
  emptyHint: ReactNode;
  className?: string;
}

export function ChatLog({ messages, emptyHint, className }: ChatLogProps) {
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  return (
    <div
      ref={logRef}
      role="log"
      aria-live="polite"
      aria-label="분석 대화 기록"
      className={cn("scroll-slim flex flex-col gap-2.5 overflow-y-auto", className)}
    >
      {messages.length === 0 ? <p className="text-xs text-fg-subtle">{emptyHint}</p> : null}

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
  );
}
