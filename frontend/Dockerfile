FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# 本地 Docker 默认跳过 Ab 登录门控；生产构建可传 --build-arg VITE_AB_LOGIN_SKIP=0
ARG VITE_AB_LOGIN_SKIP=1
ENV VITE_AB_LOGIN_SKIP=$VITE_AB_LOGIN_SKIP
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
