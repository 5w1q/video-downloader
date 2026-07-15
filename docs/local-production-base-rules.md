# 本机生产 — 基础规则

> 日期：2026-07-10  
> 场景：在本机把项目完善后，交付到**另一台 Windows 电脑**作为「本机生产」使用（Docker 本地跑，浏览器访问 `localhost`）。  
> 与「公网生产」（关 `LOCAL_MODE`、HTTPS 域名、Ab 计费、PostgreSQL）**不是同一套门槛**；公网见 `youtube-keyword-download-production-readiness.md` 等文档中的公网条款。  
> 当前基线：**仅 YouTube 关键词下载已在本机验证可下载**；其余功能按本文规则，对照 `docs/` 内既有功能/冒烟文档逐项补齐。

---

## 1. 定义与目标

| 术语 | 含义 |
|------|------|
| 本机生产 | 目标机单机 Docker 部署；`LOCAL_MODE=1`；不依赖 Ab 主站登录/计费；用户打开 `http://localhost:8080` |
| 交付机 | 当前开发/验收用的电脑 |
| 目标机 | 最终给他人使用的另一台电脑 |
| 功能完备 | 目标机上：单链解析下载、批量、YouTube / X / Instagram 关键词、（可选）AI 总结等，均能按冒烟文档通过 |

**目标：** 交付机验收通过 → 按 `local-production-transfer-checklist.md` 拷到目标机 → 只改代理端口 / Cookie / 第三方 Key 即可稳定使用。

**非目标（本场景不做）：**

- 公网域名、HTTPS、多副本 K8s  
- 关闭 `LOCAL_MODE`、强制 Ab 登录扣费  
- 打成单个 `.exe`  
- 目标机从零 `pip`/`npm` 装开发环境（允许有 Docker 即可）

---

## 2. 硬性基线（所有功能共用）

后续任何功能文档的「本机生产完善」都必须满足本节；冲突时以本节为准。

### 2.1 运行形态

1. **交付形态：Docker Compose**（优先离线镜像 + `Dockerfile.offline`）。  
2. 目标机前置：**Docker Desktop** + **本机 HTTP(S) 代理**（Clash 等，须「允许局域网」）+ 浏览器。  
3. 入口：前端 `http://localhost:8080`；后端宿主机映射以 compose 为准（当前常见 `127.0.0.1:8002→8001`）。  
4. 改 `.env` / Cookie / compose 代理后必须 **重启或重建** 对应容器，禁止「改了文件不重启」。

### 2.2 环境变量（本机生产固定取向）

| 变量 | 本机生产要求 | 说明 |
|------|--------------|------|
| `LOCAL_MODE` | **`1`** | 虚拟登录、关 Ab 计费；compose 也会注入，勿指望只改 `.env` 关掉 |
| `AB_BILLING_DISABLED` | `1` | 与上一致 |
| `DATABASE_URL` | 空 | 用 SQLite；数据在 `backend/data/`，换机不迁移属预期 |
| `FRONTEND_URL` / `PUBLIC_BASE_URL` / `APP_PUBLIC_DOMAIN` | localhost / `http://localhost:8080` | 不要填已下线公网域名 |
| `CORS_ALLOWED_ORIGINS` | 含 `http://localhost:8080` | 与前端端口一致 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 指向目标机代理 | 容器内用 `http://host.docker.internal:<端口>` |
| `YOUTUBE_COOKIEFILE` | `secrets/youtube_cookies.txt` | 与文件名一致；勿复用 B 站 Cookie |
| `YTDLP_COOKIEFILE` | 可选 B 站 | 仅下 B 站需要 |
| `YTDLP_JS_RUNTIMES` | 默认 `node` | 镜像内须有 Node + `yt-dlp-ejs` |
| 第三方 Key | 按功能启用 | X/IG→`APIFY_TOKEN`；TikTok→`SCRAPECREATORS_API_KEY`；总结→LLM/ASR |

密钥与 Cookie **只进 `backend/secrets/` 与目标机本地 `.env`，禁止提交 Git、禁止打进镜像层。**

### 2.3 运行时依赖（镜像 / 宿主机）

| 依赖 | 用途 | 缺失表现 |
|------|------|----------|
| 出口代理 | YouTube / 部分站点 / 部分 API | bot、超时、空结果 |
| `youtube_cookies.txt`（Netscape） | YouTube 下载过 bot | `Sign in to confirm you're not a bot` |
| Node（或 Deno）+ `yt-dlp-ejs` | 带 Cookie 时解 n-challenge | 仅 storyboard / `Requested format is not available` |
| ffmpeg（镜像内） | 音视频合并 | 部分 format 失败 |
| Apify / ScrapeCreators | X/IG **搜索** / TikTok **单链** | 对应功能失败；其它平台下载仍走 yt-dlp |

### 2.4 产品行为一致性（三端关键词 + 批量）

完善任一平台时，下列行为必须与 YouTube 已验收行为对齐：

1. **跳过已下载**：默认勾选；浏览器/ZIP **临时目录**下强制按历史跳过，不依赖验本地文件。  
2. **空结果**：返回可展示文案（扫描条数、日期/阈值原因），前端不卡死。  
3. **取消**：前端 abort 后有明确日志；允许「当前条服务端仍在下」。  
4. **ZIP**：成功可下；同链二次应失败（一次性）；多 worker 时令牌落盘目录须共享（compose 已挂 `backend/data`）。  
5. **错误可解释**：代理失败 / Cookie / 第三方额度 / 筛选无结果 文案可区分。  
6. **冒烟加速画质**可用低清；本机生产默认画质以产品为准，但验收至少一条真实 `ok≥1`。

### 2.5 健康与发布一致性

- `GET /api/health` 应能看到 `status=ok`，且 `build.local_mode=true`（本机生产）。  
- OpenAPI / 前后端对 `date_filter` 等枚举一致；禁止目标机跑旧镜像、交付机跑新代码却不重建镜像。

---

## 3. 功能完备度（对照表）

用本表跟踪「目标机能否宣称全功能可用」。状态随验收更新。

| 功能 | 文档 | 验收方式 | 本机生产状态 | 目标机额外依赖 |
|------|------|----------|--------------|----------------|
| YouTube 关键词搜索+下载 | `smoke-youtube-keyword-download.md`；公网优化见 `youtube-keyword-download-production-readiness.md`（仅作参考，本场景不关 LOCAL_MODE） | 浏览器按该文档最低集 | **已具备**（Cookie+代理+EJS 后可 `ok≥1`） | YouTube Cookie、代理、Node/EJS |
| X 关键词 | `x-instagram-search-integration.md` + `smoke-x-keyword-download.md` | 浏览器按该文档最低集 | **交付机最低集已过**（2026-07-10）；待目标机验收 | `APIFY_TOKEN`、代理（下载） |
| Instagram 关键词 | 同上 + `smoke-instagram-keyword-download.md` | 浏览器按该文档最低集 | **交付机最低集已过**（2026-07-10）；搜索层已换 Apify（2026-07-14） | `APIFY_TOKEN`；下载优先 CDN `video_url` |
| 批量链接/表格下载 | `smoke-bulk-download.md` | 浏览器按该文档最低集 | **待确认** | 代理；B 站另需 bilibili Cookie |
| 单链解析/下载 | `smoke-single-download.md`（**§8 当前问题汇总**） | 浏览器按该文档最低集 | **交付机最低集 29/30**（2026-07-10）；**P0：TikTok 仍 Fail** | 按站点 Cookie/代理；FB 文件名已修 |
| AI 总结（可选） | `.env.example` ASR/LLM 段 | 手工 1 条 | **可选** | LLM Key；ASR 另需公网可达 URL（本机生产常关） |

**宣称「全部功能可用」的门槛：** 上表非「可选」行在目标机均按对应冒烟 **最低集** Pass，且真实下载至少一条 `ok≥1`（允许个别日期筛选空结果）。

---

## 4. 后续功能完善规则（写文档 / 改代码时遵守）

`docs/` 里已有接入说明与冒烟时，**不要另起炉灶**；按下列顺序在交付机做完，再写入转移清单。

### 4.1 完善一个功能的固定流程

```text
1. 读本文 §2 基线 + 该功能的 integration / smoke 文档
2. 补齐依赖（Key、Cookie、镜像 Node、compose 代理）
3. 行为对齐 §2.4（跳过 / 空结果 / ZIP / 错误文案）
4. 交付机跑冒烟最低集，真实下载 ok≥1
5. 更新本文 §3 状态表 + 该 smoke 文档执行记录表
6. 按 local-production-transfer-checklist.md 导出镜像与密钥说明
```

### 4.2 文档怎么写（约束）

| 文档类型 | 要求 |
|----------|------|
| 接入说明（如 `x-instagram-search-integration.md`） | 写清：搜索供应商、环境变量、与 YouTube API/UI 对齐点、本机生产依赖 |
| 冒烟（`smoke-*.md`） | 固定参数、用例 ID、通过标准、已知限制；须含「本机生产」可执行的最低集 |
| 公网 readiness | 可保留，但须注明：**本机生产不要求关 LOCAL_MODE / 上公网域名** |
| 转移清单 | 只写拷贝与改端口步骤，不重复基线长文；依据本文 |

新增平台时：先补 integration + smoke，再改 §3 表；禁止只改代码不补冒烟。

### 4.3 代码约定（本机生产相关）

1. 关键词三端共用 `bulk_download_core` / `resolve_bulk_output`；临时目录跳过逻辑一处修、三端受益。  
2. YouTube 下载：`YOUTUBE_COOKIEFILE` + `js_runtimes`；禁止把 B 站 Cookie 塞给 YouTube。  
3. X/IG：**搜索**走托管 API，**下载**走 yt-dlp；Key 缺失时错误信息要写「缺 APIFY_TOKEN」类，而非静默挂起。  
4. secrets 只读挂载时，cookie 文件须可复制到临时可写路径（现有 downloader 逻辑）。

---

## 5. 目标机操作者规则（给使用方）

1. 只改：**代理端口**、`secrets/*.txt`、`.env` 里的 Key；不要改业务代码除非会重建镜像。  
2. Cookie 过期：浏览器重新导出 → 覆盖 `backend/secrets/youtube_cookies.txt` → `docker compose restart backend`。  
3. 代理关闭或端口变更：YouTube/外网站点会批量失败；先修代理再重试。  
4. 清空 `backend/data/`：跳过历史与 ZIP 令牌重置，属预期。  
5. 不要把 Cookie / `.env` 发到公开群或提交仓库。

详细安装步骤见：`local-production-transfer-checklist.md`。

---

## 6. 验收门槛（本机生产）

### 6.1 交付机出门前（最低）

- [ ] `GET /api/health` → `status=ok`，`local_mode=true`  
- [ ] YouTube 冒烟最低集 Pass，且至少一案 `ok≥1`（非全 skip/全 fail）  
- [ ] 镜像可导出；`youtube_cookies.txt` 与 `.env` 路径一致  
- [ ] compose 代理端口与交付机 Clash 一致并已验证  

### 6.2 目标机接手后（最低）

- [ ] 按转移清单启动；浏览器打开 `http://localhost:8080`  
- [ ] 再跑 YouTube 最低集（或手工 YT-01 + YT-S1）  
- [ ] 每启用一个新功能（X/IG/批量），跑对应 smoke 最低集后再对用户宣称可用  

### 6.3 全功能宣称前

- [ ] §3 表非可选行全部「已具备」  
- [ ] 各 smoke 执行记录表有目标机日期与 Pass  

---

## 7. 文档索引（本场景）

| 文档 | 角色 |
|------|------|
| **本文** `local-production-base-rules.md` | 基线规则；后续完善的总约束 |
| `local-production-transfer-checklist.md` | 拷到另一台的操作清单 |
| `smoke-youtube-keyword-download.md` | YouTube 冒烟（当前已打通） |
| `smoke-x-keyword-download.md` | X 冒烟 |
| `smoke-instagram-keyword-download.md` | Instagram 冒烟 |
| `smoke-bulk-download.md` | 批量下载冒烟 |
| `x-instagram-search-integration.md` | X/IG 接入规格 |
| `youtube-keyword-download-production-readiness.md` | **公网**上线清单；本机生产只借用其 P0 行为项（跳过/错误文案等），不执行「关 LOCAL_MODE」 |

---

## 8. 已知可接受限制（告知使用方）

- 「今日」等严格日期筛选易空，属平台结果波动。  
- 跳过依赖本机 `backend/data`；换机或清库后需重新积累。  
- Cookie / 第三方 Token 会过期或耗尽，需人工更换。  
- AI 总结在本机生产常关 ASR（无公网 `PUBLIC_BASE_URL`）；需要时单独开并自备可达 URL。  
- 本机生产不等于公网生产；对外提供服务须另走公网 readiness。

---

## 9. 修订约定

- 变更 §2 硬性基线时，同步检查转移清单与各 smoke「测前知悉」。  
- 某功能在目标机验收通过后，更新 §3 状态为「已具备」，并注明日期。  
- 与公网文档冲突时：面向「另一台本机」的交付以**本文**为准。
