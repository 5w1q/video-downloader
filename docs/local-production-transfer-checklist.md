# 本机生产 — 目标机交付清单

> 日期：2026-07-10  
> 依据：**[`local-production-base-rules.md`](./local-production-base-rules.md)**（硬性基线；冲突时以基线为准）  
> 目的：把本机已验收环境迁到**另一台 Windows 电脑**，操作者只改代理端口 / Cookie / Key，即可启动。  
> 功能完备度见基线 §3；当前出门最低要求为 **YouTube 可下载**，其余功能按基线 §4 对照各 smoke 文档补齐后再宣称全功能。

---

## 1. 交付物（拷什么）

### 1.1 必须

| 项 | 说明 |
|----|------|
| 项目源码（或 Git clone） | 含 `docker-compose.yml`、`backend/`、`frontend/`、`docs/` |
| Docker 镜像 tar（离线） | `video-downloader-backend:base`、`video-downloader-backend:latest`、`video-downloader-web:base`、`video-downloader-web:latest` |
| `backend/secrets/youtube_cookies.txt` | Netscape 格式；含 `LOGIN_INFO` / `__Secure-*PSID`；**勿进 Git** |
| `backend/.env` 模板 | 从本机 `.env` 复制后，在目标机改代理相关项；**勿提交 Git** |

### 1.2 按需

| 项 | 说明 |
|----|------|
| `backend/secrets/bilibili_cookies.txt` | 仅当目标机要下 B 站 |
| `APIFY_TOKEN` / `SCRAPECREATORS_API_KEY` | X / Instagram 关键词搜索 |
| LLM / ASR Key | 仅当需要 AI 总结 |

### 1.3 不要拷 / 不要指望跨机复用

| 项 | 原因 |
|----|------|
| `backend/data/`（跳过历史、ZIP 令牌、SQLite） | 换机后跳过状态重置属预期；可空目录启动 |
| `backend/downloads/` 里的临时视频 | 体积大且非运行必需 |
| `backend/.venv/`、`frontend/node_modules/`、`frontend/dist/` | 目标机用 Docker 镜像，不需要本机开发依赖 |
| `backend/scripts/smoke_*.py`、`docs/_smoke_*.log` | 交付前已清理；验收改用浏览器 + smoke 文档 |
| 本机 Clash 配置原样 | 目标机代理端口可能不同 |
| 公网域名 / 生产 JWT / 他人 Cookie | 见基线规则 §2「禁止」 |

---

## 2. 本机导出（交付前在本机执行）

在项目根（PowerShell）：

```powershell
# 1) 确认镜像存在
docker images "video-downloader-*"

# 2) 导出离线镜像（路径按需改）
New-Item -ItemType Directory -Force -Path D:\vd-transfer | Out-Null
docker save -o D:\vd-transfer\vd-backend-base.tar video-downloader-backend:base
docker save -o D:\vd-transfer\vd-backend-latest.tar video-downloader-backend:latest
docker save -o D:\vd-transfer\vd-web-base.tar video-downloader-web:base
docker save -o D:\vd-transfer\vd-web-latest.tar video-downloader-web:latest

# 3) 打包密钥与 env（单独加密介质，勿放公开网盘明文）
Copy-Item backend\secrets\youtube_cookies.txt D:\vd-transfer\
Copy-Item backend\.env D:\vd-transfer\backend.env.sample
```

源码可用 U 盘拷贝整个仓库，或目标机 `git clone` 后只拷 secrets + env + 镜像 tar。

---

## 3. 目标机安装步骤

### 3.1 前置

1. 安装 **Docker Desktop**（WSL2 后端），能跑 `docker compose version`。  
2. 安装并启动本机代理（Clash 等），开启 **允许局域网连接**。  
3. 记下 mixed/HTTP 端口（本机示例为 `7897`，目标机以实际为准）。

### 3.2 导入镜像与文件

```powershell
cd <项目根>

docker load -i D:\vd-transfer\vd-backend-base.tar
docker load -i D:\vd-transfer\vd-backend-latest.tar
docker load -i D:\vd-transfer\vd-web-base.tar
docker load -i D:\vd-transfer\vd-web-latest.tar

New-Item -ItemType Directory -Force -Path backend\secrets, backend\data, backend\downloads | Out-Null
Copy-Item D:\vd-transfer\youtube_cookies.txt backend\secrets\youtube_cookies.txt
Copy-Item D:\vd-transfer\backend.env.sample backend\.env
```

### 3.3 改配置（目标机必改）

1. **`docker-compose.yml`** 中 `HTTP_PROXY` / `HTTPS_PROXY`（及小写）端口改为目标机代理口，例如：

   `http://host.docker.internal:<端口>`

2. **`backend/.env`** 核对：

   | 变量 | 值 |
   |------|-----|
   | `LOCAL_MODE` | `1` |
   | `YOUTUBE_COOKIEFILE` | `secrets/youtube_cookies.txt` |
   | `FRONTEND_URL` / `PUBLIC_BASE_URL` | `http://localhost:8080` |
   | `CORS_ALLOWED_ORIGINS` | 含 `http://localhost:8080` |
   | `DATABASE_URL` | 空 |
   | `APIFY_TOKEN` 等 | 按需填写 |

3. Cookie 文件名与 `.env` 一致；改 Cookie / `.env` / compose 后必须 **重建或重启** 容器。

### 3.4 启动与健康检查

```powershell
cd <项目根>
docker compose up -d --build
# 或已有 latest 镜像且代码未变：docker compose up -d

Invoke-RestMethod http://127.0.0.1:8002/api/health
# 期望：status=ok，build.local_mode=true
# 浏览器：http://localhost:8080
```

---

## 4. 目标机验收（最低）

按基线规则 §6，YouTube 先过再扩其它功能。交付包**不含** Python 冒烟脚本；在目标机用浏览器验收即可。

浏览器打开 `http://localhost:8080/#keyword-download`，按 `smoke-youtube-keyword-download.md` **最低集**：

`YT-01、YT-02、YT-03、YT-05、YT-07、YT-S1、YT-P1`

通过标准见该文档 §2；空结果（尤其「今日」）可接受，须有明确提示而非卡死。

---

## 5. 运维告知（贴在目标机旁）

1. Cookie 过期 → 重新导出覆盖 `backend/secrets/youtube_cookies.txt` → `docker compose restart backend`  
2. 代理挂了 → YouTube / 部分站点全部失败  
3. Apify / ScrapeCreators 额度耗尽 → 仅对应搜索失败  
4. 清空 `backend/data` → 跳过历史与 ZIP 令牌重置  

---

## 6. 本机冒烟执行记录（交付前）

在 `smoke-youtube-keyword-download.md` §5 填写最新一次最低集结果；通过后再拷贝镜像与密钥到目标机。

| 检查项 | 本机状态 |
|--------|----------|
| `GET /api/health` local_mode=true | 交付前勾选 |
| Cookie 含 LOGIN_INFO / PSID | 交付前勾选 |
| 代理端口与 compose 一致 | 交付前勾选 |
| 镜像 `backend:latest` / `web:latest` 已导出 | 交付前勾选 |
| YouTube 冒烟最低集 Pass | 见 smoke 文档执行表 |
