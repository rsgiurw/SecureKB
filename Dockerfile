# Python FastAPI build
FROM python:3.11-slim

# [MOD] 国内网络：apt 源换阿里云镜像（deb.debian.org 国内构建不稳定；
# 阿里云为商业 CDN，长尾大包如 libllvm19 比教育网镜像节点更稳）
RUN sed -i 's@//deb.debian.org@//mirrors.aliyun.com@g; s@//security.debian.org@//mirrors.aliyun.com@g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true

# [MOD] apt 拆两层 + Acquire::Retries：弱网下单包断连自动重试 8 次；
# 前一层成功后进 Docker 层缓存，重试构建不必全部重下。
RUN apt-get -o Acquire::Retries=8 update && apt-get -o Acquire::Retries=8 install -y --no-install-recommends \
    curl \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# [MOD] Playwright/Chromium X11 系统库：随浏览器安装开关一起 opt-in。
# 原先无条件安装，但 libcairo2→libgl1→mesa→libllvm19 链条会拖入 ~100MB
# 依赖（含弱网下极易断连的 25MB libllvm19 大包），而默认部署不启用 Playwright。
ARG INSTALL_PLAYWRIGHT_BROWSERS=false
RUN if [ "$INSTALL_PLAYWRIGHT_BROWSERS" = "true" ]; then \
        apt-get -o Acquire::Retries=8 update && apt-get -o Acquire::Retries=8 install -y --no-install-recommends \
            libnss3 \
            libnspr4 \
            libatk1.0-0 \
            libatk-bridge2.0-0 \
            libcups2 \
            libdrm2 \
            libdbus-1-3 \
            libxkbcommon0 \
            libxcomposite1 \
            libxdamage1 \
            libxfixes3 \
            libxrandr2 \
            libgbm1 \
            libpango-1.0-0 \
            libcairo2 \
            libasound2 \
            libatspi2.0-0 \
            && rm -rf /var/lib/apt/lists/*; \
    else \
        echo "Skipping Playwright X11 deps. Set INSTALL_PLAYWRIGHT_BROWSERS=true to enable."; \
    fi

# 레거시 .doc(.doc→.docx) 변환 지원 (opt-in, 기본 OFF).
# docx_loader가 soffice/libreoffice 바이너리로 .doc를 변환하므로, 이 의존성이
# 없으면 .doc 업로드는 항상 ValueError가 된다(rag.yaml #26 안내와 정합).
# 기본 OFF로 이미지를 경량(~300MB 절감) 유지하되, INSTALL_DOC_SUPPORT=true일 때만
# libreoffice-writer + fonts-noto-cjk(CJK 텍스트 렌더 범용)를 설치한다.
ARG INSTALL_DOC_SUPPORT=false
RUN if [ "$INSTALL_DOC_SUPPORT" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            libreoffice-writer \
            fonts-noto-cjk \
        && rm -rf /var/lib/apt/lists/*; \
    else \
        echo "Skipping .doc conversion deps. Set INSTALL_DOC_SUPPORT=true to enable libreoffice-writer."; \
    fi

# Set working directory
WORKDIR /app

# Install uv and dependencies (as root, WITHOUT Playwright browsers yet)
# extraction-quality(pymupdf)는 순수 파이썬·경량이라 기본 포함한다. PDF 품질게이트
# (mojibake 폴백)가 env opt-in으로 켜질 때 PyMuPDF 재추출 폴백이 실제로 동작하도록
# 보장한다(게이트가 꺼져 있으면 미사용이므로 무영향).
# [MOD] 国内网络：pip/uv 默认 PyPI 换清华镜像；torch 仍走 PyTorch CPU 官方 index
# （下方 uv pip install 的显式 --index-url 优先级高于本 ENV）
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG INSTALL_LOCAL_EMBEDDING_DEPS=false
COPY pyproject.toml uv.lock ./
# [MOD] torch CPU 化：uv.lock 里 torch 2.9.1 来自 PyPI，linux-x86_64 默认 wheel 绑定 CUDA
# （连带 15 个 nvidia-* 包，镜像约 +2.5GB）。本服务只用 CPU 推理（bge reranker）：
# 过滤 CUDA 家族依赖后，从 PyTorch CPU index 单独装 torch，再装其余依赖。
# 精确钉 "torch==2.9.0+cpu"：CPU index 上 2.9.x 最高仅 2.9.0（lock 的 2.9.1 无 CPU 轮子，
# 本机 Windows 实跑 2.8.0，同为 CPU 版、偏差可接受）；且 "+cpu" 本地版本标签只有
# PyTorch 官方源存在——防止清华默认源用裸包名解析到 CUDA 版（uv 多 index 场景实测坑）。
# sentence-transformers 对 torch 仅有 >=2.0.0 约束，CPU 版满足，不会被重复拉取。
RUN pip install --no-cache-dir --upgrade pip uv && \
    if [ "$INSTALL_LOCAL_EMBEDDING_DEPS" = "true" ]; then \
        uv export --frozen --no-dev --no-emit-project --extra local-embedding --extra extraction-quality --format requirements.txt --no-hashes --output-file /tmp/requirements.txt; \
    else \
        uv export --frozen --no-dev --no-emit-project --extra extraction-quality --format requirements.txt --no-hashes --output-file /tmp/requirements.txt; \
    fi && \
    sed -i -E '/^(torch|triton|nvidia-[a-z0-9-]+)==/d' /tmp/requirements.txt && \
    uv pip install --system --no-cache "torch==2.9.0+cpu" --index https://download.pytorch.org/whl/cpu && \
    uv pip install --system --no-cache --requirement /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt

# Create runtime directories and non-root user
RUN mkdir -p logs uploads/temp && \
    useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app

ENV HF_HOME=/home/app/.cache/huggingface
# [MOD] 移除上游遗留的 TRANSFORMERS_CACHE：它会把 transformers 的缓存查找劫持到
# /home/app/.cache/huggingface（少 /hub 子目录），导致镜像内烘焙的模型全部 miss、
# 启动时反复联网重下。HF_HOME 已正确覆盖缓存定位。
# [MOD] 模型已直拷进镜像 + embedding 走智谱 API —— 离线模式让 reranker 加载
# 确定性命中缓存、启动不依赖 HF Hub 连通性。
ENV HF_HUB_OFFLINE=1
ENV TORCHINDUCTOR_CACHE_DIR=/home/app/.cache/torchinductor

# [MOD] HuggingFace 端点：国内网络默认走 hf-mirror.com，构建期模型预下载（bge）同样走它。
# 可在 compose build args 或 docker build --build-arg 用官方端点覆盖。
ARG HF_ENDPOINT=https://hf-mirror.com
ENV HF_ENDPOINT=${HF_ENDPOINT}

# Switch to non-root user before optional runtime cache downloads
USER app

# 로컬 임베딩 모델 사전 다운로드 (Qwen3-Embedding-0.6B)
# app 사용자로 다운로드하여 런타임에 캐시 접근 가능
# 약 1.2GB, HuggingFace Hub에서 다운로드
ARG PRELOAD_LOCAL_EMBEDDING_MODEL=false
RUN if [ "$PRELOAD_LOCAL_EMBEDDING_MODEL" = "true" ]; then \
    python -c "from sentence_transformers import SentenceTransformer; \
        print('📥 로컬 임베딩 모델 다운로드 중 (Qwen3-Embedding-0.6B)...'); \
        model = SentenceTransformer('Qwen/Qwen3-Embedding-0.6B', trust_remote_code=True); \
        print('✅ 임베딩 모델 다운로드 완료!')"; \
    else \
        echo "Skipping local embedding model preload. Set PRELOAD_LOCAL_EMBEDDING_MODEL=true to enable."; \
    fi

# BGE 리랭커 모델(BAAI/bge-reranker-v2-m3) 빌드타임 사전 다운로드 (opt-in, 기본 OFF).
# 미설정 시 BGE 리랭커 활성 배포의 첫 리랭킹 요청에서 모델(~수백MB)을 런타임에
# 다운로드해 cold-start 지연 + 런타임 HF Hub 가용성 의존이 생긴다. true로 켜면
# 이미지에 모델을 사전 포함해 첫 요청 지연·네트워크 의존을 제거한다(에어갭/사내망 유용).
# 주의: transformers/torch가 필요하므로 INSTALL_LOCAL_EMBEDDING_DEPS=true와 함께 켤 것.
# 기본 OFF는 이미지 크기 증가를 피하기 위한 OneRAG 경량 정책이다.
ARG PRELOAD_BGE_RERANKER_MODEL=false
# [MOD] 模型直拷：构建上下文的 models/bge-reranker-v2-m3（来源：本机 HF 缓存快照，
# .gitignore 排除不入库）直接打入镜像 HF 缓存目录，保持 hub 缓存结构
# （refs/main → snapshots/<hash>）。hf-mirror 在构建容器内不可达，直拷替代构建期下载。
# 注意：refs/main 内容必须与快照目录名一致，transformers 才能离线命中缓存。
COPY --chown=app:app models/bge-reranker-v2-m3 /home/app/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3/snapshots/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
RUN mkdir -p /home/app/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3/refs \
    && echo -n 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e > /home/app/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3/refs/main
# 镜像内已有模型时跳过下载；仅当 PRELOAD=true 且缓存缺失（如 CI 无 models/ 目录的
# 场景本 COPY 已失败）才会走到下载分支，保留作为文档化的兜底路径。
RUN if [ "$PRELOAD_BGE_RERANKER_MODEL" = "true" ] && [ ! -f /home/app/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3/snapshots/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e/model.safetensors ]; then \
        python -c "from transformers import AutoModelForSequenceClassification, AutoTokenizer; \
        model_name = 'BAAI/bge-reranker-v2-m3'; \
        print(f'📥 BGE 리랭커 모델 다운로드 중 ({model_name})...'); \
        AutoTokenizer.from_pretrained(model_name); \
        AutoModelForSequenceClassification.from_pretrained(model_name); \
        print('✅ BGE 리랭커 모델 다운로드 완료!')"; \
    else \
        echo "BGE reranker model already baked into image cache (or preload disabled)."; \
    fi

# Install Playwright browsers as app user (in /home/app/.cache/ms-playwright/)
ARG INSTALL_PLAYWRIGHT_BROWSERS=false
RUN if [ "$INSTALL_PLAYWRIGHT_BROWSERS" = "true" ]; then \
    playwright install chromium; \
    else \
        echo "Skipping Playwright browser install. Set INSTALL_PLAYWRIGHT_BROWSERS=true to enable."; \
    fi

# Copy application source after dependency and optional cache layers.
# Source-only changes should not invalidate dependency downloads.
COPY --chown=app:app . .
RUN chmod +x /app/docker-entrypoint.sh

# Set environment
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

# Start application with entrypoint script
# 진입점 스크립트가 환경변수를 올바르게 확장하고 uvicorn 실행
ENTRYPOINT ["/app/docker-entrypoint.sh"]
