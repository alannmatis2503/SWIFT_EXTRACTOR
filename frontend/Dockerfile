# frontend Dockerfile
FROM node:20 AS builder
WORKDIR /usr/src/app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund || true
COPY frontend/ ./
RUN npm run build

FROM nginx:stable-alpine
COPY --from=builder /usr/src/app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
