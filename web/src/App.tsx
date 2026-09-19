import { useCallback, useEffect, useRef, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { AskPanel, type Message, type QuickAskAction } from "@/components/AskPanel";
import { DataStatusCard } from "@/components/DataStatusCard";
import { DistrictDrawer } from "@/components/detail/DistrictDrawer";
import { OnboardingChat } from "@/components/OnboardingChat";
import { RankList } from "@/components/RankList";
import { RankMap } from "@/components/RankMap";
import { ResultSummary } from "@/components/ResultSummary";
import { SiteFooter } from "@/components/SiteFooter";
import { Button, Card, CardBody, type SelectOption } from "@/components/ui";
import {
  AGE_LABEL,
  CHARACTER_LABEL,
  INITIAL_CONDITIONS,
  PRIORITY_LABEL,
} from "@/data/businessTypes";
import { ApiError, fetchBusinessTypes, fetchParseCondition, fetchRank } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { loadConditions, saveConditions } from "@/lib/conditionsStorage";
import { fmt } from "@/lib/format";
import { b, type RichParts } from "@/lib/rich";
import type { RankResponse } from "@/types/api";
import type { Conditions } from "@/types/domain";

/* ──────────────────────────────────────────────────────────────
 * 화면 조립 + 조건 state 소유.
 *
 * 데이터는 전부 backend 에서 온다. 프론트에서 점수를 계산하지 않는다 —
 * 랭킹 로직이 backend/scoring.py 한 곳에만 있어야 화면 숫자와
 * verification_tools.py 의 판정이 어긋나지 않기 때문이다.
 *
 * 호출 정책
 *   조건 변경(자연어 입력 · 대화형 재탐색 칩) → 즉시 /rank 재호출
 *   자연어 조건 파싱                       → Claude 구조화 출력 1회 (저렴)
 *   추천 이유 생성                        → 사용자가 버튼을 눌러야 (AI 호출 비용)
 *
 * 화면 흐름 (온보딩)
 *   조건을 한 번도 입력한 적 없으면(onboarded=false) OnboardingChat을
 *   화면 전체에 띄운다 — 랭킹도, 축소된 대화 패널도 아직 없다. 여기서
 *   첫 조건이 확정되고 랭킹까지 성공하면 onboarded=true 로 바뀌면서
 *   일반 레이아웃(랭킹 + 사이드바 AskPanel)으로 넘어간다.
 *
 *   그 뒤로 조건이 바뀔 때마다 conditionsStorage.ts를 통해 로그인 사용자별로
 *   localStorage에 저장한다 — 다음 로그인부터는 이 온보딩 화면을 건너뛰고
 *   저장된 조건으로 곧장 랭킹을 보여준다. 저장된 업종이 지금은 더 이상
 *   수집돼 있지 않으면(bizOptions에 없으면) 신뢰하지 않고 다시 온보딩부터
 *   시작한다 — 없는 데이터를 있는 것처럼 보여주지 않는다는 원칙과 같다.
 * ────────────────────────────────────────────────────────────── */

const TOP_K = 20;
/** 처음엔 5곳만 보여주고, "더보기"를 누를 때마다 이만큼씩 더 펼친다.
 * 다 펼친 뒤엔 같은 버튼이 "접기"로 바뀌어 다시 5곳으로 되돌린다.
 * 서버는 이미 TOP_K(20)까지 계산해서 내려주므로 재호출 없이 펼치고 접는다. */
const INITIAL_VISIBLE = 5;
const VISIBLE_STEP = 5;

function changeMessage(
  labelParts: RichParts,
  previousTopNames: readonly string[],
  res: RankResponse,
): RichParts {
  const lead = res.results[0];
  const now = res.results.slice(0, 5).map((r) => r.district_name);
  const changed = now.filter((name) => !previousTopNames.includes(name)).length;

  return [
    ...labelParts,
    " 서울 골목상권 전체를 다시 비교했습니다. 상위 5곳 중 ",
    b(`${changed}곳`),
    "이 바뀌었고, 1위는 ",
    b(lead ? lead.district_name : "없음"),
    lead ? `(생존 안정성 ${Math.round(lead.final_score)}점)입니다.` : "입니다.",
    ...(res.warnings.length ? [" ", res.warnings.join(" ")] : []),
  ];
}

/** 자연어 문장을 파싱한 뒤 — "이렇게 이해했다"를 먼저 확인시키고 재랭킹 결과를 잇는다. */
function parseSummaryMessage(
  next: Conditions,
  bizLabel: string,
  previousTopNames: readonly string[],
  res: RankResponse,
): RichParts {
  return changeMessage(
    [
      "이해한 조건: ",
      b(bizLabel),
      ...(next.budget ? [` · 보증금 ${fmt(next.budget)}만원 이하`] : []),
      ` · ${AGE_LABEL[next.age]} · ${CHARACTER_LABEL[next.character]} · ${PRIORITY_LABEL[next.priority]}.`,
    ],
    previousTopNames,
    res,
  );
}

/** 온보딩의 첫 조건 확정 — 비교 대상(previousTopNames)이 없는 첫 랭킹이라 별도 문구를 쓴다. */
function firstParseMessage(next: Conditions, bizLabel: string, res: RankResponse): RichParts {
  const lead = res.results[0];
  if (!lead) return [`업종 "${bizLabel}"에 해당하는 상권 데이터가 없습니다.`];

  return [
    "이해한 조건: ",
    b(bizLabel),
    ...(next.budget ? [` · 보증금 ${fmt(next.budget)}만원 이하`] : []),
    ` · ${AGE_LABEL[next.age]} · ${CHARACTER_LABEL[next.character]} · ${PRIORITY_LABEL[next.priority]}. `,
    "서울 골목상권 ",
    b(`${fmt(res.n_candidates)}곳`),
    "을 비교했습니다. 1위는 ",
    b(lead.district_name),
    `(생존 안정성 ${Math.round(lead.final_score)}점)입니다. 상권을 누르면 진단과 자세한 지표를 볼 수 있어요.`,
    ...(res.warnings.length ? [" ", res.warnings.join(" ")] : []),
  ];
}

export default function App() {
  const { signOut, user } = useAuth();
  const userId = user?.id ?? null;

  const [bizOptions, setBizOptions] = useState<SelectOption[]>([]);
  const [conditions, setConditions] = useState<Conditions>(INITIAL_CONDITIONS);
  const [meta, setMeta] = useState<RankResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  /** 지금 목록/지도에 펼쳐 보여주는 개수. 새 랭킹 결과가 올 때마다 5개로 되돌린다. */
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE);
  /** 조건을 한 번이라도 확정해본 적 있는가. 저장된 조건이 있으면(복원) 곧바로 true. */
  const [onboarded, setOnboarded] = useState(false);

  const messageId = useRef(0);

  const say = useCallback((from: Message["from"], parts: RichParts) => {
    setMessages((prev) => [...prev, { id: messageId.current++, from, parts }]);
  }, []);

  /* 세션이 끊기면(401) 에러 문구를 띄우는 대신 로그인 화면으로 돌려보낸다.
   * 만료된 세션으로 계속 시도해봐야 같은 실패만 반복된다. */
  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.isUnauthorized) {
        void signOut(e.message || "세션이 만료되었습니다. 다시 로그인해 주세요.");
        return;
      }
      setError(e instanceof Error ? e.message : "잠시 후 다시 시도해 주세요.");
    },
    [signOut],
  );

  const runRank = useCallback(
    (next: Conditions, onDone?: (res: RankResponse) => void) => {
      if (!next.biz) return;

      setLoading(true);
      fetchRank(next, TOP_K)
        .then((res) => {
          setMeta(res);
          setError(null);
          onDone?.(res);
        })
        .catch(handleError)
        .finally(() => setLoading(false));
    },
    [handleError],
  );

  /* 최초 진입 — 실제로 수집된 업종 목록을 받는다. 업종을 상수로 들고 있지 않는
   * 이유: 아직 수집되지 않은 업종을 보여주면 404 를 부르는 선택지를 사용자에게
   * 내미는 셈이다.
   *
   * 저장된 조건이 있고 그 업종이 지금도 실제로 있으면 그대로 복원해 온보딩을
   * 건너뛴다. 없으면(첫 로그인이거나, 저장된 업종이 더는 없으면) 온보딩부터
   * 시작한다 — 업종을 미리 아무거나 골라두지 않고 사용자가 말해줄 때까지 기다린다. */
  const bootstrap = useCallback(() => {
    setLoading(true);
    setError(null);

    fetchBusinessTypes()
      .then((rows) => {
        const options = rows.map((r) => ({ value: r.business_code, label: r.business_name }));
        setBizOptions(options);

        if (!options[0]) {
          setError("지금은 분석할 수 있는 업종이 없습니다. 잠시 후 다시 시도해 주세요.");
          setLoading(false);
          return;
        }

        const saved = userId ? loadConditions(userId) : null;
        const restored = saved && options.some((o) => o.value === saved.biz) ? saved : null;

        if (restored) {
          setConditions(restored);
          setOnboarded(true);
          runRank(restored);
        } else {
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        handleError(e);
        setLoading(false);
      });
  }, [runRank, userId, handleError]);

  useEffect(bootstrap, [bootstrap]);

  /* 온보딩을 끝낸 뒤로는 조건이 바뀔 때마다(자연어 입력이든 칩이든) 사용자별로
   * 기억해 둔다 — 다음 로그인부터 이 조건으로 곧장 시작하기 위해서다. */
  useEffect(() => {
    if (!onboarded || !userId) return;
    saveConditions(userId, conditions);
  }, [onboarded, userId, conditions]);

  // 새 랭킹 결과가 올 때마다 다시 top 5만 보이게 접는다 — 이전 조건에서
  // "더보기"로 펼쳐뒀던 게 새 결과에도 그대로 펼쳐진 채면 어색하다.
  useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE);
  }, [meta]);

  const applyChange = (patch: Partial<Conditions>, labelParts: RichParts, userParts?: RichParts) => {
    const previousTopNames = (meta?.results ?? []).slice(0, 5).map((r) => r.district_name);
    const next = { ...conditions, ...patch };

    setConditions(next);
    if (userParts) say("user", userParts);
    runRank(next, (res) => say("bot", changeMessage(labelParts, previousTopNames, res)));
  };

  const handleReset = () => {
    const first = bizOptions[0];
    if (!first) return;

    const next = { ...INITIAL_CONDITIONS, biz: first.value };
    setConditions(next);
    say("user", ["처음 조건으로 되돌려줘"]);
    runRank(next, () =>
      say("bot", [
        `처음 조건(${first.label} · 보증금 ${fmt(INITIAL_CONDITIONS.budget)}만원 이하 · 20–30대 · 유동인구 중심 · 생존 안정성 우선)으로 되돌렸습니다.`,
      ]),
    );
  };

  const handleAsk = (action: QuickAskAction) => {
    switch (action) {
      case "otherBiz": {
        const current = bizOptions.findIndex((o) => o.value === conditions.biz);
        const nextOption = bizOptions[(current + 1) % bizOptions.length];
        if (!nextOption) return;

        applyChange(
          { biz: nextOption.value },
          ["업종을 ", b(nextOption.label), "(으)로 바꿨습니다."],
          ["다른 업종으로 보면?"],
        );
        break;
      }
      case "budget3000":
        applyChange(
          { budget: 3000 },
          ["보증금 상한을 ", b("3,000만원"), "으로 낮췄습니다."],
          ["예산을 3,000만원으로 낮추면?"],
        );
        break;
      case "age20":
        applyChange(
          { age: "20" },
          ["20대 유동인구가 많은 곳을 우선해 다시 찾았습니다."],
          ["20대 유동인구만 볼게"],
        );
        break;
      case "resident":
        applyChange(
          { character: "resident" },
          ["상권 성격을 ", b("주거 배후"), "로 바꿨습니다. 상주인구 비중이 큰 골목이 올라옵니다."],
          ["조용한 주거 배후가 좋아"],
        );
        break;
      case "reset":
        handleReset();
        break;
    }
  };

  /* 자연어 조건 입력 — OnboardingChat(첫 진입)과 AskPanel(그 이후) 모두 이 함수를
   * 그대로 쓴다. 직전 조건을 함께 보내므로 이번 문장에서 언급 안 한 필드는
   * backend가 그대로 유지해 돌려준다. */
  const handleParse = (message: string) => {
    say("user", [message]);
    setLoading(true);

    fetchParseCondition(message, conditions)
      .then((parsed) => {
        if (parsed.business_not_found) {
          setLoading(false);
          const available = bizOptions.map((o) => o.label).join(", ") || "아직 없음";
          say("bot", [
            `"${parsed.business_not_found}"은(는) 아직 수집된 업종이 아니에요. 지금 볼 수 있는 업종은 `,
            b(available),
            "입니다.",
          ]);
          return;
        }

        const bizCode = parsed.business_code ?? conditions.biz;
        if (!bizCode) {
          // 온보딩 중 업종을 아직 한 번도 말하지 않은 경우 — 업종 없이는 /rank를
          // 부를 수 없으므로(랭킹 대상이 없음) 여기서 멈추고 되묻는다.
          setLoading(false);
          say("bot", [
            "어떤 업종을 준비하고 계신지 알려주시면 바로 찾아볼게요. 예: ",
            b("치킨집"),
            ", ",
            b("카페"),
            " 등",
          ]);
          return;
        }

        const next: Conditions = {
          biz: bizCode,
          // budget 은 화면에서는 항상 숫자다(초기값 있음) — Claude가 언급 안 된 값을
          // 되돌려주지 못했을 때만 방어적으로 이전 값을 쓴다.
          budget: parsed.budget ?? conditions.budget,
          age: parsed.age,
          character: parsed.character,
          priority: parsed.priority,
        };
        const bizLabel =
          parsed.business_name ?? bizOptions.find((o) => o.value === bizCode)?.label ?? bizCode;
        const wasOnboarded = onboarded;
        const previousTopNames = (meta?.results ?? []).slice(0, 5).map((r) => r.district_name);

        setConditions(next);
        runRank(next, (res) => {
          if (!wasOnboarded) {
            setOnboarded(true);
            say("bot", firstParseMessage(next, bizLabel, res));
          } else {
            say("bot", parseSummaryMessage(next, bizLabel, previousTopNames, res));
          }
        });
      })
      .catch((e: unknown) => {
        handleError(e);
        setLoading(false);
      });
  };

  const ranking = meta?.results ?? [];
  const visibleRanking = ranking.slice(0, visibleCount);
  const selected = ranking.find((r) => r.district_code === selectedCode) ?? null;

  return (
    <>
      <div className="flex min-h-screen flex-col">
        <AppHeader />

        <main className="mx-auto w-full max-w-[86rem] flex-1 px-4 py-6 sm:px-6">
          {error ? (
            <Card variant="nodata">
              <CardBody className="flex flex-col items-start gap-3 pt-4">
                <div>
                  <p className="text-sm font-semibold text-fg">데이터를 불러오지 못했습니다</p>
                  <p className="mt-1 max-w-[62ch] text-xs leading-relaxed text-fg-muted">{error}</p>
                </div>
                <Button variant="outline" size="sm" onClick={bootstrap}>
                  다시 시도
                </Button>
              </CardBody>
            </Card>
          ) : !onboarded ? (
            <OnboardingChat
              messages={messages}
              onParse={handleParse}
              busy={loading}
              bootLoading={bizOptions.length === 0}
            />
          ) : (
            <>
              <ResultSummary meta={meta} loading={loading} conditions={conditions} />

              <div className="mt-5 grid gap-5 lg:grid-cols-12">
                <div className="flex flex-col gap-4 lg:col-span-8">
                  <RankMap
                    ranking={visibleRanking}
                    selectedCode={selectedCode}
                    onSelect={setSelectedCode}
                  />
                  <RankList
                    ranking={visibleRanking}
                    selectedCode={selectedCode}
                    onSelect={setSelectedCode}
                    loading={loading}
                  />
                  {!loading && ranking.length > INITIAL_VISIBLE ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="self-center"
                      onClick={() =>
                        setVisibleCount((c) =>
                          c < ranking.length ? Math.min(c + VISIBLE_STEP, ranking.length) : INITIAL_VISIBLE,
                        )
                      }
                    >
                      {visibleCount < ranking.length
                        ? `더보기 (${ranking.length - visibleCount}곳 더)`
                        : "접기"}
                    </Button>
                  ) : null}
                </div>

                <aside className="flex flex-col gap-4 lg:col-span-4">
                  <DataStatusCard meta={meta} />
                  <AskPanel
                    messages={messages}
                    onAsk={handleAsk}
                    onParse={handleParse}
                    businessTypeCount={bizOptions.length}
                    busy={loading}
                  />
                </aside>
              </div>
            </>
          )}
        </main>

        <SiteFooter />
      </div>

      <DistrictDrawer
        r={selected}
        conditions={conditions}
        modelVersion={meta?.model_version ?? "—"}
        asOf={meta?.as_of ?? "—"}
        onClose={() => setSelectedCode(null)}
      />
    </>
  );
}
