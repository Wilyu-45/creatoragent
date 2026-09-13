# Creator Agent Studio —— 生产镜像（多阶段构建）
#
# 为什么是多阶段：前端构建需要 Node 与 node_modules（数百 MB），
# 运行时只要「Python + dist/ 静态文件」。中间层全部丢弃后镜像小一个量级，
# 攻击面也随之变小（镜像里没有 npm、没有源码构建工具链）。
#
# 构建：
#   docker build -t creator-agent-studio:latest .
# 运行（数据落卷，否则容器重建即丢任务与记忆库）：
#   docker run -d --name creator -p 8787:8787 -v creator-data:/data \
#     -e AUTO_APPROVE=true creator-agent-studio:latest

# ------------------------------------------------------------------ #
# 阶段 1：构建前端                                                        #
# ------------------------------------------------------------------ #
FROM node:22-alpine AS web

WORKDIR /web

# 先只拷依赖清单：依赖没变时这一层可复用缓存，改源码不会重装 node_modules
COPY package.json package-lock.json ./
RUN npm ci

COPY index.html vite.config.ts tsconfig.json ./
COPY src ./src
RUN npm run build

# ------------------------------------------------------------------ #
# 阶段 2：运行时                                                        #
# ------------------------------------------------------------------ #
FROM python:3.12-slim AS runtime

# PYTHONUNBUFFERED: 日志实时输出（否则 docker logs 会延迟甚至丢日志）
# PYTHONDONTWRITEBYTECODE: 不留 .pyc，保持镜像层干净
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PIP_NO_CACHE_DIR=1 \
    CREATOR_DATA_DIR=/data \
    PORT=8787

# curl 仅用于 HEALTHCHECK（python:3.12-slim 不带）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖单独一层：requirements.txt 不变时不会因为改代码而重装
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码与前端产物
COPY app ./app
COPY scripts ./scripts
COPY golden ./golden
COPY creator.md plan.md MEMORY.md USER_GUIDE.md ./
COPY --from=web /web/dist ./dist

# 非 root 运行：容器内不需要任何特权
# /data 需要可写 —— 任务、黑板、记忆库、评估历史、检查点、trace 都落在这里
RUN useradd --create-home --uid 10001 creator \
    && mkdir -p /data \
    && chown -R creator:creator /app /data
USER creator

VOLUME ["/data"]

EXPOSE 8787

# 就绪探针：/api/health 免鉴权，因此即使开了 CREATOR_API_TOKENS 也能探活
# （start-period 给足冷启动时间：首次导入 LangGraph 与构建检查点连接约 2-3s）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

CMD ["python", "-m", "app.main"]
