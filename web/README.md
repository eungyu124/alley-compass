# 골목 컴퍼스 웹 (React 19 + TypeScript + Tailwind 4)

`backend/` FastAPI 를 호출해 상권 랭킹·진단·근거를 보여주는 화면이다.

```bash
# 1) 백엔드 먼저 (다른 터미널)
cd ../backend && uvicorn main:app --reload --port 8000

# 2) 웹
npm install
npm run dev        # http://localhost:5173
npm run build      # tsc -b && vite build
npm run typecheck  # 타입만 검사
```

기본으로 `http://localhost:8000` 의 백엔드를 본다. 다른 주소를 쓰려면
`.env.example` 을 `.env.local` 로 복사해 `VITE_API_BASE_URL` 을 바꾼다.

---

## 이 화면의 숫자는 어디서 오는가

프론트는 점수를 계산하지 않는다. 랭킹 로직이 `backend/scoring.py` 한 곳에만
있어야 화면 숫자와 `verification_tools.py` 의 판정이 어긋나지 않기 때문이다.
Supabase 도 직접 읽지 않는다 — Claude 키를 프론트 번들에 둘 수 없어 결국
FastAPI 를 거쳐야 하므로, 무료 조회까지 전부 한 경로로 모았다.

| 화면 | 엔드포인트 | 과금 | 언제 부르나 |
|---|---|---|---|
| 업종 드롭다운 | `GET /business-types` | 없음 | 최초 1회 |
| 순위 목록 · 점수 구성 | `POST /rank` | 없음 | 조건 변경 시 (예산은 500ms 디바운스) |
| 상권 진단 4영역 · 시계열 | `GET /districts/{code}/detail` | 없음 | 상세를 열 때 |
| 추천 · 반대 근거 | `POST /districts/{code}/agents` | **있음** | **버튼을 눌러야** |

`/agents` 는 Claude 를 최소 4번 부른다(추천 1 + 리스크 1 + 검증 2). 15~20초
걸리고 호출마다 과금되므로 자동 실행하지 않는다. 검증을 통과하지 못한
(`verified=false`) 문장은 화면에 내보내지 않는다 — PRD §18.

호출은 전부 `src/lib/api.ts` 를 지난다. 컴포넌트가 `fetch` 를 직접 쓰지 않는다.

### 로고 팔레트

색은 로고에서 뽑았다 — `navy`(주요 행동·선택), `teal`(데이터 강조), `gold`(브랜드 포인트).
글자에 쓰는 색은 모두 WCAG AA(4.5:1) 이상이고, 본문 기준 16px · 터치 영역 44px 이상이다.
로그인·로딩처럼 항상 짙은 네이비 위에 얹는 요소는 테마와 무관하게 고정된 `--brand-*` 토큰을 쓴다.

### 지금 진짜인 것 / 아직 아닌 것

| | 상태 |
|---|---|
| 상권·업종 목록, 순위, 점수 구성 | ✅ 실제 데이터 — `alley_compass_etl.py` 로 수집한 만큼만 나온다 |
| 상권 진단 4영역, 시간대별 유동인구, 분기별 매출·폐업률 | ✅ 실제 데이터 (`GET /districts/{code}/detail`) |
| 추천 / 반대 근거 | ✅ 실제 Claude 호출 — 버튼을 눌러야 생성. 화면 표시용일 뿐 DB엔 아직 안 쌓임 |
| 자연어 조건 입력 | ✅ 실제 Claude 구조화 출력(`POST /parse-condition`) — 이전 대화의 조건을 이어받는다 |
| 생존 안정성 Score | ✅ **LightGBM 예측**(모델 파일이 없으면 휴리스틱으로 조용히 대체). 화면에 모델 버전(`lightgbm-<버전>` / `heuristic-v0`)을 그대로 표시한다 |
| 순위(최종 점수) | ✅ 생존 안정성 Score + 상권성격·연령대 기반 타겟 적합도의 가중합 — 상권성격을 바꾸면 실제로 순위가 바뀐다 |
| 지도 | ✅ 카카오맵 — 상권 중심 좌표(중심점, 다각형 아님) + 면적 비례 원 + 순위 배지. 좌표가 없는 상권만 지도에서 빠진다(지어내지 않는다) |
| 비용 진단 (임차료·공실률) | ❌ 데이터셋에 없음 — 점선 카드로 "데이터 미보유" 표시. 대체 데이터원(R-ONE)도 정밀도가 안 맞아 보류 |
| 모델 지표 (ROC-AUC 등) | ⚠️ 학습은 끝났지만 화면엔 아직 안 보여줌 — `model_versions` 테이블에만 기록됨 |

업종이 하나만 보이면 그만큼만 수집됐다는 뜻이다. 버그가 아니므로 없는 업종을
드롭다운에 지어 넣지 않는다. 같은 이유로 "다른 업종으로 보면?" 칩은 업종이
2개 이상일 때만 만든다.

---

## 디자인 시스템

`src/components/ui/` 가 이 앱의 컴포넌트 라이브러리다. Radix Primitives 위에
우리 토큰만 입혔다 — 드롭다운·드로어·슬라이더는 접근성 함정(포커스 트랩,
화살표 순환, `aria-hidden` 관리, 화면 밖 위치 뒤집기)이 가장 많은 자리라
직접 만들면 반드시 하나는 빠진다.

```
Card      Input     Select        Field       ← 기본 3종 + 접근성 배선
Button    Badge     SegmentedControl  Slider
Meter     DataRow   Drawer        Tooltip
```

화면 코드는 항상 배럴에서 가져온다. 개별 파일 경로를 직접 import 하면 나중에
파일을 쪼갤 때 전부 고쳐야 한다.

```ts
import { Card, CardBody, Input, Select } from "@/components/ui";
```

### 주요 탭

라우터 없이 `App.tsx` 의 `tab` state 로 전환한다(`lib/tabs.ts`).

| 탭 | 하는 일 | 백엔드 |
|---|---|---|
| 추천 | 조건 바 + 순위 목록 + 지도, 상권을 누르면 상세 드로어 | `/rank`, `/detail`, `/agents` |
| 물어보기 | 문장으로 조건을 정하고 "이렇게 이해했어요" 요약을 확인 | `/parse-condition` |
| 리포트 | 상위 N곳을 PDF 로 내려받기 (AI 사용량 발생 · 버튼을 눌러야 호출) | `/report` |
| 기록 | 내가 바꿔 본 조건과 1위 상권. **이 브라우저에만 저장** (`lib/historyStorage.ts`) | 없음 (서버 목록 API 생기면 교체) |

조건을 한 번도 정하지 않은 첫 방문자는 탭 없이 `OnboardingChat` 만 본다.

### 디자인 토큰 — 3계층

```
src/styles/
  tokens.primitive.css   ① 재료  — 색 팔레트·크기 스케일. 테마와 무관
  tokens.semantic.css    ② 역할  — surface / fg / accent / positive …
                                   라이트·다크 매핑이 전부 여기 있다
  theme.css              ③ 등록  — @theme inline 으로 Tailwind 에 노출
  globals.css            진입점 — 위 셋을 import + base 레이어
```

컴포넌트는 **②의 역할 토큰만** 쓴다. `--teal-500` 을 직접 쓰지 않고 `--accent`
를 쓴다. 그래야 다크모드 대응이 `tokens.semantic.css` 한 파일 수정으로 끝난다.

**새 토큰을 추가하는 순서**

1. `tokens.primitive.css` 에 재료를 넣고
2. `tokens.semantic.css` 에 역할 이름을 붙이고 — **라이트·다크 둘 다**
3. `theme.css` 에 한 줄 등록한다

```css
/* ① 재료 */
--navy-500: #14375c;

/* ② 역할 */
:root                    { --accent: var(--navy-500); }   /* 라이트 */
:root[data-theme="dark"] { --accent: var(--teal-300); }   /* 다크 */

/* ③ 등록 → bg-accent / text-accent / border-accent 가 생긴다 */
@theme inline { --color-accent: var(--accent); }
```

`@theme` 뒤의 `inline` 이 핵심이다. 없으면 Tailwind 가 선언 시점의 값을 복사해
버려 다크모드에서 색이 바뀌지 않는다. `inline` 이면 유틸리티가 `var(--accent)`
를 그대로 참조한다.

> 규칙 하나: **어떤 토큰도 다크 블록에만 존재해선 안 된다.** 라이트에 없는
> 토큰이 다크에만 있으면 라이트 화면에서 색이 통째로 비어 렌더된다.

토큰 이름은 Tailwind v4 네임스페이스(`--text-*` · `--radius-*` · `--shadow-*`)와
겹치지 않게 지었다. 글자색이 `--text` 가 아니라 `--fg` 인 이유다.

### 테마는 세 상태다

`system`(기본, 아무 속성도 없음) / `light` / `dark`. 둘만 두면 "OS 따라가기"로
돌아갈 방법이 사라진다. `src/lib/useTheme.ts` 가 `data-theme` 속성을 관리하고,
토큰 쪽이 `:root` · `@media (prefers-color-scheme: dark)` ·
`[data-theme="dark"]` 세 블록을 전부 정의해 둔다.

### 접근성

- `Field` 가 `id` · `aria-describedby` · `aria-invalid` 를 만들어 context 로
  내려준다. 컨트롤은 `useFieldControl()` 로 받아간다 — 손으로 이으면 반드시
  빠뜨린다.
- 포커스 링은 `:focus-visible` 로만 뜨고 어디서도 지우지 않는다.
- 차트 SVG 는 `role="img"` + 값이 들어간 `aria-label` 을 갖는다.
- 순위 행은 통째로 버튼이라 Tab 한 번에 하나씩 넘어간다.
- 대화 로그는 `aria-live` — 조건을 바꾸면 화면 다른 쪽이 통째로 바뀌는데
  스크린리더 사용자는 그 변화를 알 방법이 없다.
- `prefers-reduced-motion` 을 존중한다.

---

## 구조

```
src/
  lib/
    api.ts           ← 백엔드 호출 유일 접점 (access_token 을 Bearer 로 싣는다)
    supabase.ts      Supabase 클라이언트 — 로그인 전용 · 메일 링크 URL 판독
    auth.tsx         로그인 상태(AuthProvider · useAuth)
    authErrors.ts    Supabase 오류 → 한국어 문장
    cn.ts            Tailwind 클래스 병합 (clsx + tailwind-merge)
    format.ts        숫자 포맷 · Score 톤 판정
    rich.ts          근거 문장의 조각 배열 표현
    useTheme.ts      system / light / dark
  types/
    api.ts           backend/schemas.py 와 1:1
    domain.ts        조건 유니온 타입 (백엔드 Literal 과 일치)
    ui.ts            디자인 시스템 공통 어휘 (Size / Tone)
  components/
    ui/              ← 디자인 시스템
    charts/          SeriesChart(선/막대) · CompetitionChart
    detail/          DistrictDrawer · DiagnosticGrid · RankingFactors · AgentPanel
    auth/            SignInScreen · UpdatePasswordScreen · AuthLayout · PasswordInput
                     SocialButton (카카오·구글 가이드 준수) · AccountMenu
    legal/           PrivacyPage — /privacy, 로그인 없이 열림
    brand/           LogoMark · Wordmark · CompassArt (로고에서 뽑은 브랜드 요소)
    tabs/            RecommendTab · AskTab · ReportTab · HistoryTab (주요 탭 4개)
    AppHeader (탭 내비) · TabBar (모바일 하단 탭) · ConditionBar · RankList
    ResultSummary · RankMap · ScoreRing · LoadingScreen · OnboardingChat
    ChatLog · SiteFooter · Rich · ThemeToggle
  Root.tsx           공개 페이지 분기(/privacy) + 로그인 관문
                     (loading / recovery / authenticated / unauthenticated)
                     App 은 lazy — 로그인 전에는 받지 않는다
  App.tsx            조건 state 소유 · API 호출 조립
```

조건 state 는 `App.tsx` 가 단독으로 소유하고 나머지는 전부 props 를 받는다.

---

## 로그인

서비스 전체가 로그인 필수다. 로그인하지 않으면 백엔드를 한 번도 부르지 않는다
(`Root.tsx`). 인증은 Supabase Auth, 토큰 검증은 `backend/auth.py` 가 한다.

**설정** — `.env.example` 을 `.env.local` 로 복사해 `VITE_SUPABASE_URL`,
`VITE_SUPABASE_ANON_KEY` 를 채운다. 예시값(`YOUR_…`)이 남아 있으면 미설정으로
보고 로그인 화면에 설정 안내를 띄운다. Supabase 대시보드에서는

- Authentication → URL Configuration → **Redirect URLs** 에 `http://localhost:5173/`
  (배포 주소도) 를 넣는다. 없으면 가입 확인·재설정 메일의 링크가 Site URL 로 가버린다.
- Authentication → Providers → Email 의 최소 비밀번호 길이를 **8** 로 맞춘다
  (`lib/authErrors.ts` 의 `MIN_PASSWORD_LENGTH`, `backend/scripts/create_user.py` 와 같은 값).
- 소셜 로그인은 켠 것만 `VITE_AUTH_PROVIDERS` 에 적는다.

| 흐름 | 화면 |
|---|---|
| 로그인 · 가입 · 재설정 메일 요청 | `SignInScreen` — 한 화면에서 모드 전환 |
| 가입 확인 메일 | 도착 안 하면 같은 화면에서 재발송. 이미 가입된 이메일은 Supabase 가 오류 대신 성공처럼 응답하므로 `identities` 가 빈 것으로 구분한다 |
| 재설정 링크로 돌아옴 | `UpdatePasswordScreen` — 세션이 있어도 새 비밀번호를 정하기 전엔 앱을 열지 않는다 |
| 만료된 링크 · 소셜 로그인 취소 | URL 의 `error_code` 를 읽어 로그인 화면에 표시하고 주소창에서 지운다 |
| API 가 401 을 줌 | 로그아웃 후 로그인 화면에 백엔드가 준 사유를 표시 |

계정을 운영자가 직접 만들려면 `backend/scripts/create_user.py` 를 쓴다.
DB 쪽(가입 시 `profiles` 자동 생성, 검색 기록 쓰기 권한 축소)은
`db/schema_v1.1.sql` 끝의 **v1.3 패치**다.

**소셜 로그인 버튼**은 서비스 강조색을 쓰지 않는다. 카카오(#FEE500 · 말풍선 ·
"카카오 로그인")와 구글(흰/검정 · G 로고)이 가이드로 지정하고 검수 때 확인한다.
색은 `--kakao` / `--google*` 토큰으로만 쓴다.

**개인정보처리방침** (`/privacy`) 은 구글 앱 게시·카카오 비즈 앱 전환에 필요하다.
⚠️ 공개 전에 `components/legal/PrivacyPage.tsx` 의 `OPERATOR`(운영자명·문의 메일)를
실제 값으로 채운다. 수집 항목·파기·외부 전송 조항은 코드 동작과 맞춰 두었으니,
코드를 바꾸면 이 페이지도 함께 고친다.

### 배포 시

- 호스팅은 **모든 경로를 `index.html` 로** 돌려줘야 한다(SPA fallback). 안 그러면
  `/privacy` 가 404 다. Vercel·Netlify·Cloudflare Pages 는 설정 한 줄이면 된다.
- Supabase **Redirect URLs**, Google 클라이언트의 **JavaScript 원본**, 카카오
  **Web 플랫폼 도메인**, 백엔드 `CORS_ORIGINS` 에 배포 주소를 추가한다.

---

## 근거 문장을 HTML 문자열로 쓰지 않는 이유

```ts
["점포당 배후수요 ", b("2.9"), ", 서울 평균보다 15% 높음"]
```

조각 배열로 두고 `<Rich/>` 가 렌더한다. `dangerouslySetInnerHTML` 이 필요 없고,
평문만 필요한 곳은 `plain(parts)` 로 같은 문장을 태그 없이 얻는다.

---

## 빈칸을 그대로 두는 화면

없는 데이터를 지어내지 않는 것이 이 프로젝트의 원칙이라 화면에도 빈칸이
드러난다. `Card` 의 `nodata` 변형(점선 테두리)이 이 상태 전용이다. 회색으로
죽이지 않고 "비어 있음이 의도된 것"으로 보이게 한다.

분기가 1개뿐이라 추세를 못 만드는 경우처럼, 이유는 백엔드가 `reason` 으로
내려준 문장을 그대로 보여준다. 프론트가 변명을 지어내지 않는다.
