# AI Team Orchestrator dashboard — multi-stage: Node builds the Vite bundle,
# nginx serves the static files and proxies /api + /ws to the backend service.
FROM node:20-alpine AS build
WORKDIR /app

# Lockfile-accurate install first for layer caching.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
