import { Loader2, SendHorizontal } from "lucide-react";
import { useState } from "react";

import { Button, Input } from "@/components/ui";

import { ChatLog, type Message } from "./ChatLog";

/* ──────────────────────────────────────────────────────────────
 * 첫 진입 화면 — 조건을 아직 한 번도 입력한 적 없는 사용자용 풀스크린 채팅.
 *
 * 여기서 첫 조건이 확정되면(App.tsx의 handleParse가 랭킹까지 성공시키면)
 * 이 화면은 사라지고 일반 레이아웃(랭킹 + 축소된 AskPanel)으로 넘어간다.
 * 그 뒤로는 이 조건을 localStorage에 기억해 두므로, 다음 로그인부터는
 * 이 화면을 다시 보지 않는다(conditionsStorage.ts).
 *
 * onParse는 App.tsx의 handleParse 그대로다 — AskPanel과 로직을 공유하고
 * 화면 배치만 다르다.
 * ────────────────────────────────────────────────────────────── */

export interface OnboardingChatProps {
  messages: readonly Message[];
  onParse: (message: string) => void;
  /** 이번 문장의 파싱·재랭킹이 진행 중인가 */
  busy: boolean;
  /** 업종 목록을 아직 못 받아왔나 — 이때는 입력을 막는다(검증할 목록이 없음) */
  bootLoading: boolean;
}

export function OnboardingChat({ messages, onParse, busy, bootLoading }: OnboardingChatProps) {
  const [draft, setDraft] = useState("");
  const disabled = busy || bootLoading;

  const submit = () => {
    const message = draft.trim();
    if (!message || disabled) return;
    onParse(message);
    setDraft("");
  };

  return (
    <div className="mx-auto flex min-h-[70vh] w-full max-w-xl flex-col justify-center gap-6 px-4 py-10">
      <div className="text-center">
        <h1 className="text-2xl font-semibold text-fg">어떤 창업을 준비하고 계세요?</h1>
        <p className="mt-2 text-sm leading-relaxed text-fg-muted">
          업종·예산·타깃을 문장으로 편하게 말씀해 주세요. 예: "치킨집 열고 싶은데 예산 3,000만원
          정도로 어디가 좋을까"
        </p>
      </div>

      {messages.length > 0 ? (
        <ChatLog messages={messages} emptyHint="" className="max-h-80" />
      ) : null}

      <div className="flex gap-1.5">
        <Input
          size="lg"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) submit();
          }}
          placeholder={bootLoading ? "업종 정보를 불러오는 중…" : "조건을 문장으로 입력하세요"}
          disabled={disabled}
          wrapperClassName="flex-1"
          aria-label="조건을 문장으로 입력"
        />
        <Button
          variant="solid"
          size="lg"
          disabled={disabled || !draft.trim()}
          onClick={submit}
          aria-label="시작하기"
        >
          {busy ? (
            <Loader2 aria-hidden="true" className="size-4 animate-spin" />
          ) : (
            <SendHorizontal aria-hidden="true" className="size-4" />
          )}
        </Button>
      </div>
    </div>
  );
}
