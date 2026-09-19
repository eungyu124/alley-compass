-- =========================================================
-- 골목 컴퍼스 (Alley Compass)
-- Supabase / PostgreSQL Schema v1.1
--
-- v1.1 변경사항:
--   - 접근 제어 전략을 명시적으로 문서화
--   - anon 역할에 대한 개인 세션 테이블 접근을 명시적으로 revoke
--     (기존에도 grant가 없어 결과적으로 막혀 있었으나, 의도를 코드로 명확히 함)
--
-- v1.2: service_role 명시적 GRANT (신형 secret key 대응)
-- v1.3: 로그인 — profiles 테이블·가입 트리거, 검색 기록 쓰기 권한을
--       FastAPI(service_role) 전용으로 축소. 파일 끝 섹션만 따로 실행 가능.
-- =========================================================


-- =========================================================
-- 1. DISTRICTS
-- 서울 골목상권 기본 정보
-- =========================================================
create table public.districts (
    id bigint generated always as identity primary key,

    district_code varchar(30) not null unique,
    district_name varchar(100) not null,
    gu_name varchar(50),

    latitude double precision,
    longitude double precision,

    created_at timestamptz not null default now()
);


-- =========================================================
-- 2. BUSINESS_TYPES
-- 업종 마스터
-- =========================================================
create table public.business_types (
    id bigint generated always as identity primary key,

    business_code varchar(30) not null unique,
    business_name varchar(100) not null,
    category varchar(100),

    created_at timestamptz not null default now()
);


-- =========================================================
-- 3. DISTRICT_FEATURES
-- 상권 × 업종 × 시점 Feature Table
-- Pandas 전처리 결과 저장
-- =========================================================
create table public.district_features (
    id bigint generated always as identity primary key,

    district_id bigint not null
        references public.districts(id)
        on delete cascade,

    business_type_id bigint not null
        references public.business_types(id)
        on delete cascade,

    reference_date date not null,

    -- 유동인구
    foot_traffic double precision,
    foot_traffic_20 double precision,
    foot_traffic_30 double precision,

    -- 배후인구
    resident_population double precision,
    worker_population double precision,

    -- 점포
    store_count integer,
    opening_rate double precision,
    closure_rate double precision,

    -- 매출
    estimated_sales double precision,
    sales_growth_rate double precision,

    -- 경쟁
    competition_density double precision,

    -- 시설 / 접근성
    facility_count integer,
    transit_score double precision,

    -- 확장 Feature
    extra_features jsonb not null default '{}'::jsonb,

    -- 데이터셋별 기준시점
    -- 예:
    -- {
    --   "foot_traffic": "2024-06",
    --   "sales": "2024-Q2",
    --   "stores": "2024",
    --   "worker_population": "2024-H1"
    -- }
    source_dates jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now(),

    constraint uq_district_feature
        unique (
            district_id,
            business_type_id,
            reference_date
        )
);


-- =========================================================
-- 4. MODEL_VERSIONS
-- LightGBM 모델 버전 및 평가 지표
-- =========================================================
create table public.model_versions (
    id bigint generated always as identity primary key,

    model_name varchar(100) not null default 'LightGBM',
    version varchar(50) not null unique,

    target_name varchar(100),

    roc_auc double precision,
    pr_auc double precision,
    brier_score double precision,
    calibration_error double precision,

    train_start_date date,
    train_end_date date,
    validation_start_date date,
    validation_end_date date,

    feature_names jsonb not null default '[]'::jsonb,
    model_metadata jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now()
);


-- =========================================================
-- 5. PREDICTIONS
-- 모델이 계산한 상권 × 업종 안정성 예측
-- =========================================================
create table public.predictions (
    id bigint generated always as identity primary key,

    feature_id bigint not null
        references public.district_features(id)
        on delete cascade,

    model_version_id bigint not null
        references public.model_versions(id)
        on delete cascade,

    -- 모델 raw probability
    risk_probability double precision
        check (
            risk_probability is null
            or risk_probability between 0 and 1
        ),

    -- UI용 안정성 Score: 0~100
    stability_score double precision
        check (
            stability_score is null
            or stability_score between 0 and 100
        ),

    confidence_score double precision
        check (
            confidence_score is null
            or confidence_score between 0 and 100
        ),

    predicted_at timestamptz not null default now(),

    constraint uq_prediction
        unique (feature_id, model_version_id)
);


-- =========================================================
-- 6. SEARCH_SESSIONS
-- 사용자가 입력한 창업 조건
--
-- user_id는 nullable:
-- 서비스는 로그인 필수이고 FastAPI가 항상 user_id를 채운다.
-- 그래도 nullable로 두는 이유는 탈퇴 시 on delete set null로
-- 기록과 사람의 연결만 끊고 조건 기록은 남기기 위해서다(v1.3 ③).
--
-- [접근 전략 - MVP]
-- 이 테이블 이하(search_sessions, recommendation_runs,
-- recommendations, agent_analyses, verification_claims)는
-- 개인 세션 데이터이므로 anon/authenticated의 직접 접근을
-- 허용하지 않는다. 모든 쓰기/읽기는 FastAPI가 service_role로
-- 대행하며, 세션 소유권 검증은 애플리케이션 레벨(세션 id를
-- 쿠키/토큰으로 관리)에서 수행한다. service_role은 RLS를
-- 우회하므로 아래 정책은 service_role 요청에는 영향을 주지
-- 않는다.
--
-- 아래 "to authenticated" 정책은 향후 실제 로그인 기능을
-- 도입해 프론트가 Supabase Auth 세션으로 직접 조회하는
-- 경로를 열 때 그대로 사용하기 위해 남겨둔다. MVP 기간에는
-- 프론트가 이 경로를 사용하지 않으므로 사실상 비활성 상태다.
-- =========================================================
create table public.search_sessions (
    id uuid primary key default gen_random_uuid(),

    user_id uuid
        references auth.users(id)
        on delete set null,

    business_type_id bigint
        references public.business_types(id)
        on delete set null,

    budget numeric(14, 2),

    target_age varchar(30),
    market_character varchar(50),
    priority varchar(50),

    -- 향후 조건 추가용
    preferences jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now()
);


-- =========================================================
-- 7. RECOMMENDATION_RUNS
-- 같은 세션에서 조건을 변경할 때마다 새로운 추천 Run 생성
-- =========================================================
create table public.recommendation_runs (
    id uuid primary key default gen_random_uuid(),

    session_id uuid not null
        references public.search_sessions(id)
        on delete cascade,

    model_version_id bigint
        references public.model_versions(id)
        on delete set null,

    -- 실제 Ranking에 사용된 조건 Snapshot
    conditions jsonb not null default '{}'::jsonb,

    execution_time_ms integer,

    created_at timestamptz not null default now()
);


-- =========================================================
-- 8. RECOMMENDATIONS
-- 각 Run의 최종 Top-K 추천 결과
-- =========================================================
create table public.recommendations (
    id bigint generated always as identity primary key,

    run_id uuid not null
        references public.recommendation_runs(id)
        on delete cascade,

    district_id bigint not null
        references public.districts(id)
        on delete cascade,

    rank integer not null
        check (rank > 0),

    -- ML 기반 생존 안정성
    stability_score double precision
        check (
            stability_score is null
            or stability_score between 0 and 100
        ),

    -- 예산 / 타깃 / 선호조건 등을 합친 최종 Ranking Score
    final_score double precision
        check (
            final_score is null
            or final_score between 0 and 100
        ),

    budget_fit boolean,

    target_fit_score double precision
        check (
            target_fit_score is null
            or target_fit_score between 0 and 100
        ),

    -- Ranking에 사용된 세부 점수
    score_breakdown jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now(),

    constraint uq_run_rank
        unique (run_id, rank),

    constraint uq_run_district
        unique (run_id, district_id)
);


-- =========================================================
-- 9. AGENT_ANALYSES
--
-- Recommendation Agent
-- Risk Agent
-- Verification Agent 관련 결과 저장
-- =========================================================
create table public.agent_analyses (
    id bigint generated always as identity primary key,

    recommendation_id bigint not null
        references public.recommendations(id)
        on delete cascade,

    agent_type varchar(30) not null
        check (
            agent_type in (
                'recommendation',
                'risk',
                'verification'
            )
        ),

    content text not null,

    -- Claude가 구조화된 결과를 반환할 경우 저장
    structured_output jsonb not null default '{}'::jsonb,

    model_name varchar(100),

    created_at timestamptz not null default now()
);


-- =========================================================
-- 10. VERIFICATION_CLAIMS
--
-- Verification Agent가 추출한 개별 Claim과
-- Tool 계산 결과 기록
-- =========================================================
create table public.verification_claims (
    id bigint generated always as identity primary key,

    analysis_id bigint not null
        references public.agent_analyses(id)
        on delete cascade,

    claim_text text not null,

    metric varchar(100),

    -- 예:
    -- percentile
    -- trend
    -- raw_value
    -- budget
    -- competition
    verification_type varchar(50),

    claimed_value double precision,
    actual_value double precision,

    -- 숫자로 표현하기 어려운 값 대응
    claimed_text varchar(255),
    actual_text varchar(255),

    tolerance double precision,

    verified boolean not null default false,

    verification_tool varchar(100),
    verification_reason text,

    created_at timestamptz not null default now()
);



-- =========================================================
-- INDEX
-- =========================================================

create index idx_districts_code
on public.districts(district_code);

create index idx_business_types_code
on public.business_types(business_code);


create index idx_features_district
on public.district_features(district_id);

create index idx_features_business
on public.district_features(business_type_id);

create index idx_features_reference_date
on public.district_features(reference_date);

create index idx_features_district_business
on public.district_features(
    district_id,
    business_type_id
);


create index idx_predictions_feature
on public.predictions(feature_id);

create index idx_predictions_model
on public.predictions(model_version_id);

create index idx_predictions_stability
on public.predictions(stability_score desc);


create index idx_search_sessions_user
on public.search_sessions(user_id);

create index idx_search_sessions_created
on public.search_sessions(created_at desc);


create index idx_runs_session
on public.recommendation_runs(session_id);

create index idx_runs_created
on public.recommendation_runs(created_at desc);


create index idx_recommendations_run
on public.recommendations(run_id);

create index idx_recommendations_district
on public.recommendations(district_id);

create index idx_recommendations_rank
on public.recommendations(run_id, rank);


create index idx_agent_recommendation
on public.agent_analyses(recommendation_id);

create index idx_agent_type
on public.agent_analyses(agent_type);


create index idx_verification_analysis
on public.verification_claims(analysis_id);

create index idx_verification_verified
on public.verification_claims(verified);



-- =========================================================
-- ROW LEVEL SECURITY
-- =========================================================

alter table public.districts
enable row level security;

alter table public.business_types
enable row level security;

alter table public.district_features
enable row level security;

alter table public.model_versions
enable row level security;

alter table public.predictions
enable row level security;

alter table public.search_sessions
enable row level security;

alter table public.recommendation_runs
enable row level security;

alter table public.recommendations
enable row level security;

alter table public.agent_analyses
enable row level security;

alter table public.verification_claims
enable row level security;



-- =========================================================
-- PUBLIC DATA READ POLICIES
--
-- 상권 / 업종 / Feature / 모델 / Prediction은
-- 공개 데이터로 사용. anon 포함 누구나 직접 조회 가능
-- (프론트가 지도/상권 정보를 FastAPI 경유 없이 바로
-- 렌더링할 수 있도록 허용)
-- =========================================================

create policy "Public can read districts"
on public.districts
for select
to anon, authenticated
using (true);


create policy "Public can read business types"
on public.business_types
for select
to anon, authenticated
using (true);


create policy "Public can read district features"
on public.district_features
for select
to anon, authenticated
using (true);


create policy "Public can read model versions"
on public.model_versions
for select
to anon, authenticated
using (true);


create policy "Public can read predictions"
on public.predictions
for select
to anon, authenticated
using (true);



-- =========================================================
-- SEARCH SESSION POLICIES
--
-- 실제 쓰기는 FastAPI가 service_role로 수행한다(RLS 우회).
-- ⚠️ 아래 insert/update/delete 정책은 v1.3 패치에서 제거된다 —
-- 로그인이 켜진 뒤로는 사용자가 FastAPI를 우회해 기록을 쓰는
-- 경로가 되기 때문이다. 남는 것은 본인 기록 select뿐이다.
-- =========================================================

create policy "Users can read own search sessions"
on public.search_sessions
for select
to authenticated
using (
    auth.uid() = user_id
);


create policy "Users can create own search sessions"
on public.search_sessions
for insert
to authenticated
with check (
    auth.uid() = user_id
);


create policy "Users can update own search sessions"
on public.search_sessions
for update
to authenticated
using (
    auth.uid() = user_id
)
with check (
    auth.uid() = user_id
);


create policy "Users can delete own search sessions"
on public.search_sessions
for delete
to authenticated
using (
    auth.uid() = user_id
);



-- =========================================================
-- RECOMMENDATION RUN READ POLICY (MVP 기간 사실상 비활성 — 위와 동일한 이유)
-- 본인 Session에 연결된 Run만 조회
-- =========================================================

create policy "Users can read own recommendation runs"
on public.recommendation_runs
for select
to authenticated
using (
    exists (
        select 1
        from public.search_sessions s
        where s.id = recommendation_runs.session_id
          and s.user_id = auth.uid()
    )
);



-- =========================================================
-- RECOMMENDATIONS READ POLICY (MVP 기간 사실상 비활성 — 위와 동일한 이유)
-- =========================================================

create policy "Users can read own recommendations"
on public.recommendations
for select
to authenticated
using (
    exists (
        select 1
        from public.recommendation_runs rr
        join public.search_sessions s
          on s.id = rr.session_id
        where rr.id = recommendations.run_id
          and s.user_id = auth.uid()
    )
);



-- =========================================================
-- AGENT ANALYSIS READ POLICY (MVP 기간 사실상 비활성 — 위와 동일한 이유)
-- =========================================================

create policy "Users can read own agent analyses"
on public.agent_analyses
for select
to authenticated
using (
    exists (
        select 1
        from public.recommendations r
        join public.recommendation_runs rr
          on rr.id = r.run_id
        join public.search_sessions s
          on s.id = rr.session_id
        where r.id = agent_analyses.recommendation_id
          and s.user_id = auth.uid()
    )
);



-- =========================================================
-- VERIFICATION CLAIM READ POLICY (MVP 기간 사실상 비활성 — 위와 동일한 이유)
-- =========================================================

create policy "Users can read own verification claims"
on public.verification_claims
for select
to authenticated
using (
    exists (
        select 1
        from public.agent_analyses aa
        join public.recommendations r
          on r.id = aa.recommendation_id
        join public.recommendation_runs rr
          on rr.id = r.run_id
        join public.search_sessions s
          on s.id = rr.session_id
        where aa.id = verification_claims.analysis_id
          and s.user_id = auth.uid()
    )
);



-- =========================================================
-- DATA API PERMISSIONS
-- Automatically expose new tables를 꺼둔 경우를 대비
-- =========================================================

grant usage on schema public
to anon, authenticated;


-- 공개 데이터
grant select
on public.districts,
   public.business_types,
   public.district_features,
   public.model_versions,
   public.predictions
to anon, authenticated;


-- 사용자 검색 Session
-- MVP 기간에는 FastAPI(service_role)만 실제로 사용하며,
-- 아래 grant는 향후 로그인 기능 도입 시를 위해 authenticated에만 부여한다.
grant select, insert, update, delete
on public.search_sessions
to authenticated;


-- 결과 데이터
-- 쓰기는 FastAPI(service_role)가 수행하고
-- 사용자는 읽기만 수행 (마찬가지로 향후 로그인 기능용)
grant select
on public.recommendation_runs,
   public.recommendations,
   public.agent_analyses,
   public.verification_claims
to authenticated;


-- Identity Column 사용에 필요한 Sequence 권한
grant usage, select
on all sequences in schema public
to authenticated;


-- =========================================================
-- anon 역할의 개인 세션 데이터 접근을 명시적으로 차단
-- (기존에도 grant가 없어 결과적으로 막혀 있었으나,
--  "MVP에서 anon은 이 테이블들에 절대 직접 접근하지 않는다"는
--  설계 의도를 코드로 명확히 하기 위해 명시적으로 revoke)
-- =========================================================

revoke all
on public.search_sessions,
   public.recommendation_runs,
   public.recommendations,
   public.agent_analyses,
   public.verification_claims
from anon;


-- =========================================================
-- v1.2 패치 — service_role 명시적 GRANT
--
-- service_role은 RLS를 우회하지만, 그건 "정책 검사를 건너뛴다"는
-- 뜻이지 "테이블 GRANT가 자동으로 생긴다"는 뜻이 아니다. 신형
-- Secret API Key(sb_secret_...)를 쓰는 프로젝트에서는 이 GRANT가
-- 없으면 FastAPI(service_role)조차 "permission denied"를 받는다.
-- 기존 프로젝트에 적용할 때는 이 섹션만 SQL Editor에 붙여넣어도 된다.
-- =========================================================

grant usage on schema public to service_role;

grant select, insert, update, delete
on public.districts,
   public.business_types,
   public.district_features,
   public.model_versions,
   public.predictions,
   public.search_sessions,
   public.recommendation_runs,
   public.recommendations,
   public.agent_analyses,
   public.verification_claims
to service_role;

grant usage, select
on all sequences in schema public
to service_role;


-- =========================================================
-- v1.3 패치 — 로그인 (Supabase Auth)
--
-- 서비스 전체가 로그인 필수가 되면서 바뀌는 것 세 가지.
-- 기존 프로젝트에는 이 섹션만 SQL Editor 에 붙여넣어 실행하면
-- 된다. 여러 번 실행해도 안전하다(if exists / or replace).
--
--   ① profiles       화면에 보여줄 이름·사진. 가입하면 트리거가 만든다.
--   ② 권한 축소      로그인한 사용자가 anon key 로 검색 기록을 직접
--                    쓰거나 지우지 못하게 한다. 쓰기는 FastAPI 만.
--   ③ search_sessions.user_id 는 그대로 on delete set null.
--                    탈퇴하면 계정은 지워지고, 검색 조건 기록은
--                    누구의 것인지 연결을 끊은 채 모델 학습용으로
--                    남는다(PRD §22 Data Flywheel). 개인정보처리방침
--                    (web /privacy)의 파기 조항과 같은 내용이다.
--
-- 회원 테이블을 따로 만들지 않는다 — 계정·비밀번호·소셜 연동은
-- Supabase 가 auth.users / auth.identities 에서 관리한다.
-- =========================================================


-- ① profiles ────────────────────────────────────────────────

create table if not exists public.profiles (
    -- auth.users 와 1:1. 탈퇴하면 같이 지워진다.
    id uuid primary key
        references auth.users(id)
        on delete cascade,

    email text,
    display_name text,
    avatar_url text,

    -- 처음 가입한 방식: email / google / kakao
    provider text,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

comment on table public.profiles is
    '로그인 사용자의 표시 정보. auth.users 가입 시 트리거(handle_new_user)가 생성한다.';


-- 가입 시 profiles 한 줄을 만든다.
-- 소셜 로그인은 이름·사진을 raw_user_meta_data 로 넘겨준다.
-- 카카오는 name 대신 nickname 으로 올 수 있어 순서대로 찾는다.
--
-- security definer: auth 스키마 트리거는 가입 요청자 권한으로 돌기
-- 때문에, public.profiles 에 쓰려면 함수 소유자 권한이 필요하다.
-- search_path 를 비워 두는 것은 security definer 함수의 표준 방어다.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.profiles (id, email, display_name, avatar_url, provider)
    values (
        new.id,
        new.email,
        coalesce(
            new.raw_user_meta_data ->> 'full_name',
            new.raw_user_meta_data ->> 'name',
            new.raw_user_meta_data ->> 'nickname',
            split_part(new.email, '@', 1)
        ),
        coalesce(
            new.raw_user_meta_data ->> 'avatar_url',
            new.raw_user_meta_data ->> 'picture'
        ),
        coalesce(new.raw_app_meta_data ->> 'provider', 'email')
    )
    on conflict (id) do nothing;

    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;

create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_user();


-- 패치 적용 전에 이미 가입한 사용자도 profiles 를 갖게 한다.
insert into public.profiles (id, email, display_name, avatar_url, provider)
select
    u.id,
    u.email,
    coalesce(
        u.raw_user_meta_data ->> 'full_name',
        u.raw_user_meta_data ->> 'name',
        u.raw_user_meta_data ->> 'nickname',
        split_part(u.email, '@', 1)
    ),
    coalesce(u.raw_user_meta_data ->> 'avatar_url', u.raw_user_meta_data ->> 'picture'),
    coalesce(u.raw_app_meta_data ->> 'provider', 'email')
from auth.users u
on conflict (id) do nothing;


-- updated_at 자동 갱신
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists profiles_touch_updated_at on public.profiles;

create trigger profiles_touch_updated_at
before update on public.profiles
for each row execute function public.touch_updated_at();


-- profiles 접근: 본인 것만 읽고, 이름만 고칠 수 있다.
-- email / provider 는 auth.users 가 원본이라 사용자가 바꾸면 어긋난다.
alter table public.profiles enable row level security;

drop policy if exists "Users can read own profile" on public.profiles;
create policy "Users can read own profile"
on public.profiles
for select
to authenticated
using ((select auth.uid()) = id);

drop policy if exists "Users can update own profile" on public.profiles;
create policy "Users can update own profile"
on public.profiles
for update
to authenticated
using ((select auth.uid()) = id)
with check ((select auth.uid()) = id);

revoke all on public.profiles from anon, authenticated;
grant select on public.profiles to authenticated;
grant update (display_name, avatar_url) on public.profiles to authenticated;
grant select, insert, update, delete on public.profiles to service_role;


-- ② 검색 기록 권한 축소 ─────────────────────────────────────
--
-- v1.1 은 "향후 로그인 도입용"으로 authenticated 에 쓰기·삭제까지
-- 열어 두었다. 로그인이 실제로 켜진 지금, 그대로 두면 사용자가
-- FastAPI 를 거치지 않고 anon key + 자기 토큰으로 search_sessions 에
-- 임의 조건을 넣거나 기록을 지울 수 있다 — 학습 데이터 오염이다.
-- 기록은 FastAPI(service_role)만 쓰고, 사용자는 본인 것 읽기만 한다.

drop policy if exists "Users can create own search sessions" on public.search_sessions;
drop policy if exists "Users can update own search sessions" on public.search_sessions;
drop policy if exists "Users can delete own search sessions" on public.search_sessions;

revoke insert, update, delete on public.search_sessions from authenticated;


-- (부가) 사용자별 기록 조회를 빠르게 — 내 검색 기록 화면 대비
create index if not exists idx_search_sessions_user_created
on public.search_sessions(user_id, created_at desc);


-- =========================================================
-- v1.4 패치 — 상권 좌표/구/면적 (지도)
--
-- alley_compass_etl.py가 긁어오는 6종 데이터(유동인구·매출·점포·시설·
-- 직장인구·상주인구)에는 좌표가 없어 gu_name/latitude/longitude가 계속
-- NULL이었다. 별도 스크립트 district_geo.py가 서울시 상권분석서비스
-- (TbgisTrdarRelm, "영역-상권")로 채운다 — 이름과 달리 다각형이 아니라
-- 중심점 좌표 + 면적만 주므로, area_m2를 새로 추가해 지도에서 원(circle)
-- 반지름을 면적에 비례시키는 데 쓴다. 기존 프로젝트에는 이 섹션만
-- SQL Editor에 붙여넣어도 된다(여러 번 실행해도 안전).
-- =========================================================

alter table public.districts
    add column if not exists area_m2 double precision;

comment on column public.districts.area_m2 is
    '상권 면적(㎡). 서울시 상권분석서비스(영역-상권, TbgisTrdarRelm)의 RELM_AR. district_geo.py가 채운다.';


-- =========================================================
-- 완료
-- =========================================================
