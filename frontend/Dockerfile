# Railway 최적화된 프론트엔드 Dockerfile
# Multi-stage build for production optimization

# Stage 1: Dependencies 설치
FROM node:20-alpine AS deps
WORKDIR /app

# package.json과 package-lock.json만 먼저 복사 (의존성 캐싱 최적화)
COPY package*.json ./

# [MOD] 国内网络：npm 走 npmmirror 镜像（官方源在国内构建容器内不稳定）
RUN npm config set registry https://registry.npmmirror.com

# 프로덕션 의존성만 설치 (개발 의존성 제외)
RUN npm ci --only=production --frozen-lockfile

# Stage 2: Build stage
FROM node:20-alpine AS builder
WORKDIR /app

# [MOD] 默认语言构建期注入：Vite 会把 VITE_ 变量烘焙进产物。
# dev 模式由 frontend/.env 提供；容器构建上下文不含 .env，必须经 build arg 传入。
ARG VITE_DEFAULT_LOCALE=zhHans
ENV VITE_DEFAULT_LOCALE=${VITE_DEFAULT_LOCALE}

# 전체 의존성 설치 (devDependencies 포함)
COPY package*.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci --frozen-lockfile

# 소스 코드 복사
COPY . .

# TypeScript 및 ESLint 검사 (빌드 시 오류 조기 발견)
RUN npm run lint

# 런타임 config.js는 entrypoint에서 생성하므로 빌드 시 배포 URL을 강제하지 않는다.
RUN npm run build:warning-gate

# Stage 3: Production runtime
FROM nginx:alpine AS production

# 불필요한 nginx 기본 파일 제거
RUN rm -rf /usr/share/nginx/html/*

# 빌드된 파일을 nginx html 디렉토리에 복사
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx 설정 파일 복사
COPY nginx.conf /etc/nginx/conf.d/default.conf.template

# entrypoint 스크립트 복사 (런타임 config.js 생성용)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Railway에서 동적으로 할당하는 포트 사용
ENV PORT=80
EXPOSE 80

# entrypoint.sh 실행 (config.js 생성 후 nginx 시작)
ENTRYPOINT ["/entrypoint.sh"]
