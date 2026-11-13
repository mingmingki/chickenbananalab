# The Earth 게임 서버 — 시뮬레이션이 서버 안에서 쉬지 않고 돌아가므로 인스턴스는 꼭 1대만, 꺼지지 않게 운영한다.
FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY server ./server
COPY public ./public
# 게임 세계·계정 DB는 /data 에 (호스팅의 영구 디스크를 여기에 연결)
ENV DATA_DIR=/data PORT=8080
RUN mkdir -p /data
EXPOSE 8080
STOPSIGNAL SIGTERM
CMD ["node", "--disable-warning=ExperimentalWarning", "server/index.js"]
