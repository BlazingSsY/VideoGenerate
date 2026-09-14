# ---------- 第一阶段：构建前端 ----------
FROM node:20-alpine AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 第二阶段：运行后端 + 静态前端 ----------
FROM python:3.11-slim

ARG APP_VERSION=1.6.1
LABEL org.opencontainers.image.title="video-generate" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data \
    FRONTEND_DIST=/app/frontend_dist

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend_dist

RUN mkdir -p /app/data/uploads /app/data/videos

EXPOSE 8008

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8008'))" || exit 1

# 用 exec 保证 uvicorn 是 PID 1，能正确收到 SIGTERM。
# BEHIND_PROXY=true 时启用 --proxy-headers，让后端能读到真实客户端 IP 与协议。
CMD ["sh", "-c", "exec uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8008} $([ \"$BEHIND_PROXY\" = \"true\" ] && echo \"--proxy-headers --forwarded-allow-ips=*\")"]
