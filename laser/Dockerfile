# Laser API (server.mjs): simulation + customer GLB. The app itself is static (npm run build -> dist/).
FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY server.mjs ./
COPY src/ src/
ENV HOST=0.0.0.0 PORT=8001
USER node
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD wget -qO- http://127.0.0.1:8001/api/laser/info > /dev/null || exit 1
CMD ["node", "server.mjs"]
