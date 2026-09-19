import { useEffect, useRef, useState } from "react";

import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui";
import { type KakaoMapInstance, loadKakaoMaps } from "@/lib/kakaoMaps";
import type { DistrictScore } from "@/types/api";

/* ──────────────────────────────────────────────────────────────
 * 랭킹 결과를 지도에 원(circle)으로 표시한다 (F-06 보조 뷰).
 *
 * 서울시 "영역-상권" API가 실제로는 다각형이 아니라 중심점 좌표 + 면적만
 * 주는 걸 확인해서(district_geo.py 참고), 정밀한 상권 모양 대신 중심점에
 * 면적 비례 원을 그리는 근사로 간다. 색은 생존 안정성 Score 톤
 * (lib/format.ts의 scoreTone과 같은 기준: 75/60)을 그대로 쓴다 — 원 색이
 * 목록의 점수 색과 다르면 같은 데이터를 다르게 말하는 셈이라서다.
 *
 * 원만 찍어서는 "어디가 어딘지" 알 수 없어서 두 가지를 더한다.
 *   ① 순위 배지 — 목록의 순위 숫자와 그대로 맞춰서, 지도의 몇 번 원이
 *     목록의 몇 번 행인지 바로 대응되게 한다.
 *   ② 마우스를 올리면 상권명 + 점수를 말풍선(InfoWindow)으로 보여준다.
 *     클릭하면(목록 행 클릭과 동일하게) 상세 Drawer가 열린다.
 *
 * district_geo.py로 아직 좌표가 안 채워진 상권은 지도에서 그냥 빠진다 —
 * 없는 좌표를 지어내 임의 위치에 찍지 않는다.
 * ────────────────────────────────────────────────────────────── */

export interface RankMapProps {
  ranking: readonly DistrictScore[];
  selectedCode: string | null;
  onSelect: (code: string) => void;
}

const SEOUL_CENTER = { lat: 37.5665, lng: 126.978 };

/** 캔버스 그리기용 실제 색 값이 필요해서, CSS 커스텀 프로퍼티를 계산된 값으로 읽는다.
 * 라이트/다크 어느 쪽이든 브라우저가 이미 계산해 둔 값을 그대로 가져오므로
 * 여기서 테마를 따로 분기하지 않아도 된다. */
function readColorVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function scoreColor(score: number, colors: { high: string; mid: string; low: string }): string {
  if (score >= 75) return colors.high;
  if (score >= 60) return colors.mid;
  return colors.low;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function RankMap({ ranking, selectedCode, onSelect }: RankMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<KakaoMapInstance | null>(null);
  // Circle과 CustomOverlay(순위 배지)는 둘 다 setMap(null)로 지운다.
  const overlaysRef = useRef<Array<{ setMap: (map: KakaoMapInstance | null) => void }>>([]);
  const infoWindowRef = useRef<InstanceType<(typeof window.kakao.maps)["InfoWindow"]> | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // SDK 로드 + 지도 인스턴스 생성. 한 번만 한다.
  useEffect(() => {
    let cancelled = false;

    loadKakaoMaps()
      .then((kakao) => {
        if (cancelled || !containerRef.current) return;
        mapRef.current = new kakao.maps.Map(containerRef.current, {
          center: new kakao.maps.LatLng(SEOUL_CENTER.lat, SEOUL_CENTER.lng),
          level: 8,
        });
        infoWindowRef.current = new kakao.maps.InfoWindow({ removable: false, zIndex: 10 });
        setStatus("ready");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setErrorMessage(e instanceof Error ? e.message : "지도를 불러오지 못했습니다.");
        setStatus("error");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  // 랭킹·선택 상태가 바뀔 때마다 원 + 순위 배지를 다시 그린다.
  useEffect(() => {
    if (status !== "ready" || !mapRef.current) return;
    const kakao = window.kakao;
    const map = mapRef.current;
    const infoWindow = infoWindowRef.current;

    overlaysRef.current.forEach((overlay) => overlay.setMap(null));
    overlaysRef.current = [];
    infoWindow?.close();

    const withGeo = ranking.filter(
      (r): r is DistrictScore & { latitude: number; longitude: number } =>
        r.latitude != null && r.longitude != null,
    );
    if (withGeo.length === 0) return;

    const colors = {
      high: readColorVar("--score-high", "#4b7d5f"),
      mid: readColorVar("--score-mid", "#3f8f8a"),
      low: readColorVar("--score-low", "#b5533f"),
    };
    const accent = readColorVar("--accent", "#0f766e");

    const bounds = new kakao.maps.LatLngBounds();

    withGeo.forEach((r) => {
      const position = new kakao.maps.LatLng(r.latitude, r.longitude);
      bounds.extend(position);

      const isSelected = r.district_code === selectedCode;
      const score = Math.round(r.final_score);
      const color = scoreColor(r.final_score, colors);
      // 면적(㎡) → 반지름(m) 근사(원 넓이 공식 역산). 너무 작거나 커서 안
      // 보이는 걸 막기 위해 60~400m로 클램프한다 — 지도 축척과 무관하게
      // 항상 눈에 띄는 크기를 보장하려는 것이라 실제 면적과는 비례하되
      // 절대 크기는 근사치다.
      const radius = r.area_m2 ? Math.min(400, Math.max(60, Math.sqrt(r.area_m2 / Math.PI))) : 120;

      const circle = new kakao.maps.Circle({
        center: position,
        radius,
        strokeWeight: isSelected ? 3 : 1,
        strokeColor: isSelected ? accent : color,
        strokeOpacity: 0.9,
        fillColor: color,
        fillOpacity: isSelected ? 0.55 : 0.3,
      });
      circle.setMap(map);

      // 순위 배지 — 목록의 순위 숫자와 그대로 맞춘다. CustomOverlay의 content는
      // 실제 페이지 DOM에 붙으므로 Tailwind 클래스를 그대로 쓸 수 있다
      // (Circle과 달리 캔버스가 아니다).
      const badge = new kakao.maps.CustomOverlay({
        position,
        content: `<div class="flex size-6 items-center justify-center rounded-full border-2 border-white font-mono text-2xs font-semibold text-white shadow" style="background:${color}">${r.rank}</div>`,
        yAnchor: 0.5,
        zIndex: isSelected ? 20 : 10,
      });
      badge.setMap(map);

      const showInfo = () => {
        if (!infoWindow) return;
        infoWindow.setContent(
          `<div class="px-2.5 py-1.5 text-xs">` +
            `<p class="font-semibold text-fg">${r.rank}위 · ${escapeHtml(r.district_name)}</p>` +
            `<p class="mt-0.5 text-fg-muted">${escapeHtml(r.gu_name ?? "")} · 생존 안정성 ${score}점</p>` +
            `</div>`,
        );
        infoWindow.setPosition(position);
        infoWindow.open(map);
      };
      const hideInfo = () => infoWindow?.close();

      kakao.maps.event.addListener(circle, "mouseover", showInfo);
      kakao.maps.event.addListener(circle, "mouseout", hideInfo);
      kakao.maps.event.addListener(circle, "click", () => onSelect(r.district_code));

      overlaysRef.current.push(circle, badge);
    });

    map.setBounds(bounds);
  }, [ranking, selectedCode, status, onSelect]);

  return (
    <Card>
      <CardHeader divided>
        <CardTitle as="h3" className="text-sm">
          지도로 보기
        </CardTitle>
      </CardHeader>

      <CardBody className="p-0">
        {status === "error" ? (
          <p className="p-4 text-xs leading-relaxed text-fg-muted">{errorMessage}</p>
        ) : (
          <div
            ref={containerRef}
            role="img"
            aria-label="추천 상권 위치를 원으로 표시한 지도. 숫자는 목록의 순위와 같고, 원 크기는 상권 면적, 색은 생존 안정성 점수를 나타낸다."
            className="h-[380px] w-full"
          />
        )}
      </CardBody>

      {status === "ready" ? (
        <p className="border-t border-border-subtle px-4 py-2 text-2xs text-fg-subtle">
          숫자 = 목록 순위 · 색 = 생존 안정성 점수(초록 75+ · 청록 60~74 · 빨강 60 미만) · 원 크기 = 상권
          면적. 원에 마우스를 올리면 이름이 뜨고, 누르면 상세 정보가 열립니다.
        </p>
      ) : null}
    </Card>
  );
}
