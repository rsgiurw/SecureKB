#!/bin/sh
# Docker 진입점 스크립트
# 목적: 환경변수 확장 및 애플리케이션 실행
# 중요: main.py를 직접 호출하여 PORT 환경변수를 올바르게 처리

# 0. logs 디렉토리 생성 (존재하지 않으면)
mkdir -p /app/logs

# 1. LLM API 키 확인 및 안내
# [MOD] SecureKB：LLM 为智谱 GLM（ZHIPU_API_KEY），OpenAI key 仅作兼容备用
if [ -z "$ZHIPU_API_KEY" ] && [ -z "$OPENAI_API_KEY" ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  ⚠️  未检测到 LLM API 密钥                                       ║"
    echo "╠══════════════════════════════════════════════════════════════════╣"
    echo "║                                                                  ║"
    echo "║  RAG 系统的回答生成功能需要 API 密钥。                           ║"
    echo "║                                                                  ║"
    echo "║  🔑 智谱开放平台（本项目默认 LLM）：                              ║"
    echo "║     https://open.bigmodel.cn                                     ║"
    echo "║                                                                  ║"
    echo "║  📝 设置方法：                                                   ║"
    echo "║     1. 在 .env 文件添加：ZHIPU_API_KEY=\"your-api-key\"            ║"
    echo "║     2. 重启容器：docker compose -f docker-compose.securekb.yml restart api ║"
    echo "║                                                                  ║"
    echo "║  ℹ️  检索功能无需 API 密钥也可使用。                              ║"
    echo "║                                                                  ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""
fi

# 2. 배치 크롤러 백그라운드 실행 (명시적 opt-in)
if [ "${START_BATCH_CRAWLER:-false}" = "true" ]; then
    echo "🚀 Starting batch crawler in background..."
    # stdout과 파일에 동시 출력 (Railway 로그에서도 확인 가능)
    python -m app.batch.main 2>&1 | tee /app/logs/batch-startup.log &
    BATCH_PID=$!
    echo "✅ Batch crawler started (PID: $BATCH_PID)"
    echo "📋 Batch logs: /app/logs/batch-startup.log"
else
    echo "ℹ️  Batch crawler disabled. Set START_BATCH_CRAWLER=true to enable it."
fi

# 3. FastAPI 서버 시작
echo "🌐 Starting FastAPI server..."
exec python main.py
