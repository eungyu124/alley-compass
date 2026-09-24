# 골목 컴퍼스 백엔드 — Render(Docker 런타임) 배포용.
#
# Docker로 가는 이유: backend/report.py가 쓰는 WeasyPrint는 Pango/Cairo 같은
# OS 라이브러리가 필요한데, Render의 기본 Python 런타임(비-Docker)은 이런
# apt 패키지를 설치할 방법이 없다. Dockerfile이면 apt-get으로 바로 넣을 수 있다.
#
# 빌드 컨텍스트는 저장소 루트다 — backend/main.py가 sys.path로 끌어오는
# alley_compass_etl/ 을 같이 COPY해야 하기 때문이다(둘이 형제 디렉터리).
# bookworm(Debian 12)으로 고정한다 — libgdk-pixbuf 패키지명이 Debian 13(trixie)부터
# libgdk-pixbuf-2.0-0으로 바뀌어서, "slim"만 쓰면 베이스가 바뀔 때 이 목록이 깨진다.
FROM python:3.11-slim-bookworm

# WeasyPrint 런타임 의존성 + 한글 PDF 렌더링용 폰트 + LightGBM의 OpenMP 런타임.
# (macOS 로컬 개발에서 WeasyPrint는 "brew install pango", LightGBM은
#  "brew install libomp"로 각각 해결했던 것과 같은 종류의 라이브러리들 —
#  여기 libgomp1이 리눅스에서의 libomp에 해당한다. backend/report.py의
#  _patch_macos_library_path()는 Darwin 전용이라 여기선 안 쓰인다.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-nanum \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements만 먼저 복사해서 의존성 레이어를 캐싱한다 — 코드만 바뀐
# 재배포에서는 pip install을 다시 안 돌게 하려는 것.
COPY backend/requirements.txt backend/requirements.txt
COPY alley_compass_etl/requirements.txt alley_compass_etl/requirements.txt
RUN pip install --no-cache-dir \
    -r backend/requirements.txt \
    -r alley_compass_etl/requirements.txt

COPY backend/ backend/
COPY alley_compass_etl/ alley_compass_etl/

WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1

# Render가 컨테이너에 주입하는 PORT로 바인딩한다(고정 포트를 쓰면 배포가 실패한다).
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
