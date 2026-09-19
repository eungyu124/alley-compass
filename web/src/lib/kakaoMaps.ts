/* ──────────────────────────────────────────────────────────────
 * 카카오맵 JS SDK 로더.
 *
 * 별도 npm 패키지(react-kakao-maps-sdk 등)를 쓰지 않고 스크립트 태그를
 * 직접 넣는다 — 카카오 SDK 자체가 window.kakao 전역을 만드는 방식이라
 * 래퍼 없이도 충분하고, 이 프로젝트 다른 서드파티 연동(Supabase 등)도
 * 공식 SDK를 그대로 쓰는 정도라 굳이 의존성을 늘리지 않는다.
 *
 * autoload=false로 받고 kakao.maps.load()로 직접 초기화한다 — 스크립트가
 * 로드되자마자 지도를 그리려 들면 컨테이너 DOM이 아직 없을 수 있어서다.
 *
 * 여러 컴포넌트가 동시에 지도를 띄워도 스크립트는 한 번만 삽입한다.
 * ────────────────────────────────────────────────────────────── */

declare global {
  interface Window {
    kakao: typeof kakao;
  }
}

// 카카오 SDK가 만드는 전역 네임스페이스. 공식 타입 패키지를 쓰지 않으므로
// 이 파일 안에서 실제로 쓰는 만큼만 최소한으로 선언한다.
declare namespace kakao.maps {
  class LatLng {
    constructor(lat: number, lng: number);
  }
  class LatLngBounds {
    extend(latlng: LatLng): void;
  }
  class Map {
    constructor(container: HTMLElement, options: { center: LatLng; level: number });
    setBounds(bounds: LatLngBounds): void;
  }
  class Circle {
    constructor(options: {
      center: LatLng;
      radius: number;
      strokeWeight?: number;
      strokeColor?: string;
      strokeOpacity?: number;
      strokeStyle?: string;
      fillColor?: string;
      fillOpacity?: number;
    });
    setMap(map: Map | null): void;
  }
  class CustomOverlay {
    constructor(options: {
      position: LatLng;
      content: string | HTMLElement;
      xAnchor?: number;
      yAnchor?: number;
      zIndex?: number;
    });
    setMap(map: Map | null): void;
  }
  class InfoWindow {
    constructor(options: {
      position?: LatLng;
      content?: string | HTMLElement;
      removable?: boolean;
      zIndex?: number;
    });
    setPosition(position: LatLng): void;
    setContent(content: string | HTMLElement): void;
    open(map: Map): void;
    close(): void;
  }
  const event: {
    addListener: (target: unknown, type: string, handler: () => void) => void;
  };
  function load(callback: () => void): void;
}

/** RankMap.tsx 등에서 ref 타입으로 쓰기 위한 별칭. */
export type KakaoMapInstance = InstanceType<typeof kakao.maps.Map>;
export type KakaoOverlay = { setMap: (map: KakaoMapInstance | null) => void };

let loadPromise: Promise<typeof kakao> | null = null;

export function loadKakaoMaps(): Promise<typeof kakao> {
  if (loadPromise) return loadPromise;

  const appkey = import.meta.env.VITE_KAKAO_MAP_APPKEY as string | undefined;
  if (!appkey) {
    return Promise.reject(
      new Error(
        "지도를 표시하려면 VITE_KAKAO_MAP_APPKEY가 필요합니다. web/.env.local에 카카오 개발자 " +
          "콘솔의 JavaScript 키를 넣어주세요.",
      ),
    );
  }

  loadPromise = new Promise((resolve, reject) => {
    if (window.kakao?.maps?.LatLng) {
      resolve(window.kakao);
      return;
    }

    const existing = document.querySelector<HTMLScriptElement>("script[data-kakao-maps-sdk]");
    const script = existing ?? document.createElement("script");

    if (!existing) {
      script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${appkey}&autoload=false`;
      script.dataset.kakaoMapsSdk = "true";
      document.head.appendChild(script);
    }

    script.addEventListener("load", () => {
      window.kakao.maps.load(() => resolve(window.kakao));
    });
    script.addEventListener("error", () => {
      loadPromise = null; // 실패했으면 다음 시도에서 다시 삽입하게 한다
      reject(new Error("카카오맵 SDK를 불러오지 못했습니다. 도메인 등록·JS 키를 확인하세요."));
    });
  });

  return loadPromise;
}
