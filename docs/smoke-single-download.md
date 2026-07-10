# 单链解析 / 下载 — 冒烟测试

> 日期：2026-07-10  
> 范围：主站「粘贴链接 → 解析 → 选清晰度 → 立即下载」**单条**能力，含同屏 **AI 总结 / 字幕 / 导图 / 问答**（不含关键词搜索、不含表格批量）  
> 入口：首页 Hero 输入框 / `#` 顶栏回到主站后粘贴链接  
> 相关：`HeroSection.vue`、`VideoResult.vue`、`VideoSummary.vue`、`POST /api/parse`、`POST /api/download`、`GET /api/proxy/thumbnail`、总结相关 API  
> 本机生产完善顺序：[`local-production-base-rules.md`](./local-production-base-rules.md) §4 → 本文最低集 → 更新基线 §3「单链解析/下载」状态

---

## 0. 功能清单（须覆盖）

| 能力 | UI / 行为 | 冒烟是否覆盖 |
|------|-----------|--------------|
| 粘贴公开视频链接 | Hero 输入框；空链接时「解析」禁用 | ✅ |
| 解析视频 | `POST /api/parse`；展示标题 / 作者 / 平台 / 时长等 | ✅ 每平台至少 1 次 |
| 缩略图 | 经 `/api/proxy/thumbnail` 展示（防盗链） | ✅ 有封面的平台 |
| 清晰度 / 格式列表 | 至少 1 个可选 format；可切换选中 | ✅ |
| 立即下载 | `POST /api/download` → 浏览器落盘可播文件 | ✅ 每平台至少 1 次 |
| 下载中态 | 按钮「下载中…」、不可重复点 | ✅ |
| 解析失败提示 | 无效 / 私密 / 风控链有明确错误，不白屏 | ✅ |
| 抖音短链 | `v.douyin.com/...` 可解析（专用模块） | ✅ DY |
| B 站短链 / BV | `b23.tv` 或 `BV...` | ✅ BL（至少一种） |
| X / Twitter 域名 | `x.com` 与 `twitter.com` 等价可下 | ✅ X |
| AI 总结摘要 | 解析后点「AI 总结」→「总结摘要」有正文 | ✅ 见 §4.3 |
| 字幕文本 | 「字幕文本」Tab；有分句或可解释占位 | ✅ |
| 字幕导出 | SRT / VTT / TXT 至少一种可下 | ✅ |
| 思维导图 | 「思维导图」Tab 可生成；PNG/SVG 导出至少一种 | ✅ |
| AI 问答 | 「AI 问答」Tab 提问有回答 | ✅ |

> 关键词下载、表格批量见各自 smoke 文档，**不在本文范围**。

---

## 1. 固定参数与样例链接

| 项 | 值 |
|----|-----|
| 环境 | 前端可达后端（如 `http://localhost:8080`）；浏览器允许下载 |
| 健康检查 | `GET /api/health` → `status=ok` |
| 每平台链接数 | **1** 条公开可播视频（冒烟规模） |
| 清晰度 | 列表**第一项**（或最低可用画质，加速） |
| Cookie / 代理 | 按 §5 平台依赖表；缺依赖导致 Fail 记备注，勿改成 Pass |
| AI / LLM | 已配置 `DEEPSEEK_API_KEY` 或 `SUMMARIZE_LLM_API_KEY` 等（见 `.env.example`） |
| ASR（无平台字幕时） | 阿里云 Paraformer 等按环境配置；无字幕平台仍须能出总结或明确失败文案 |
| 登录 / 额度 | `LOCAL_MODE` 下按产品门闸；若需登录则先登录再测 AI |

**测前自备公开链接（勿用需登录私密链；链接会过期，执行前换新）：**

| 平台 ID | 平台 | 建议 URL 形态 | 备注 |
|---------|------|---------------|------|
| DY | 抖音 | `https://v.douyin.com/...` 或 `www.douyin.com/video/...` | 走 `douyin` 专用解析，一般**无需** Cookie |
| TT | TikTok | `https://www.tiktok.com/@.../video/...` | 优先 ScrapeCreators（`SCRAPECREATORS_API_KEY`）；HK 代理下 yt-dlp 网页不可用 |
| YT | YouTube | `https://www.youtube.com/watch?v=...` 或 `youtu.be/...` | 建议 `YOUTUBE_COOKIEFILE` + 代理 + Node/EJS；**优先作 AI 主测链**（常有字幕） |
| BL | Bilibili | `https://www.bilibili.com/video/BV...` 或 `b23.tv/...` | 建议 `BILIBILI_COOKIEFILE`；**可作 AI 备选**（平台字幕） |
| X | X (Twitter) | `https://x.com/.../status/...` | yt-dlp；代理；须为**视频帖** |
| IG | Instagram | `https://www.instagram.com/reel/...` 或 `/p/...` | 建议 Cookie；优先公开 Reel |
| FB | Facebook | `https://www.facebook.com/.../videos/...` 或 `fb.watch/...` | yt-dlp；公开视频；常需代理 / Cookie |

> Hero 示例按钮（YouTube / Bilibili / 抖音）可作快速入口，但验收以本表自备链为准。

**AI 用例推荐载体：** 优先 **YouTube（有字幕）**；备选 **Bilibili**。抖音 / X 可能走 desc 占位 + ASR，仍须跑通总结链路。

---

## 2. 通过标准（通用）

### 2.1 解析 / 下载

- [ ] 粘贴合法 URL → 点「解析视频」→ 出现结果区（左栏信息 + 右栏总结面板）
- [ ] 标题非空；`platform` 标签与站点一致（或可识别别名，如 Twitter→X）
- [ ] 有封面时缩略图可见（或加载失败不挡下载）
- [ ] `formats` ≥ 1；选中一项后「立即下载」可点
- [ ] 下载完成后浏览器得到可打开的视频文件
- [ ] 下载中按钮禁用且文案为进行中；结束后恢复
- [ ] 失败时有可读错误（解析失败 / 下载失败），页面不卡死

**单平台下载 Pass：** 「解析成功 + 至少一次下载落盘成功」。仅解析成功不算 Pass。

### 2.2 AI 同屏（须测）

- [ ] 「AI 总结」可触发；「总结摘要」出现非空正文（Markdown/HTML 渲染正常）
- [ ] 「字幕文本」有分句列表，或弱占位 / ASR 失败时有**可解释**提示（不白屏、不无限转圈）
- [ ] 有可用字幕时，SRT / VTT / TXT **至少一种**导出成功
- [ ] 「思维导图」生成成功；PNG 或 SVG **至少一种**可导出
- [ ] 「AI 问答」输入一问，得到非空回答（或额度/登录的明确提示且产品设计如此——本机生产应配好 Key，以**有回答**为 Pass）

**AI Pass 定义：** `SD-AI-01`～`SD-AI-05` 全部 Pass（在约定载体链上各跑一次即可，不必每平台重复全套 AI）。

---

## 3. 主用例矩阵（按平台 · 下载）

每平台固定流程：

```text
粘贴 URL → 解析视频 → 核对信息/封面/格式 → 选清晰度 → 立即下载 → 确认文件可播
```

| ID | 平台 | 步骤要点 | 期望 |
|----|------|----------|------|
| SD-DY-01 | 抖音 | 短链或 video 页；选默认 format 下载 | 解析成功；文件可播；平台显示抖音相关 |
| SD-TT-01 | TikTok | 公开 video 链；代理开启 | 解析成功；文件可播 |
| SD-YT-01 | YouTube | watch / youtu.be；Cookie+代理就绪 | 解析成功；多清晰度可选；下载可播 |
| SD-BL-01 | Bilibili | BV 或 b23；Cookie 就绪 | 解析成功；下载可播（无 412 或已用 Cookie 绕过） |
| SD-X-01 | X | status 链（x.com 或 twitter.com） | 解析成功；下载可播（视频帖，非纯图文） |
| SD-IG-01 | Instagram | 公开 Reel / 含视频的 Post | 解析成功；下载可播 |
| SD-FB-01 | Facebook | 公开视频页或 fb.watch | 解析成功；下载可播 |

**下载最低集：** 上表 **全部 7 平台**（`SD-DY-01` … `SD-FB-01`）。无代理时 TT/FB 记 Fail+备注并复测，**不得**用 Skip 代替出门验收（除非书面注明环境无外网代理且产品声明不支持）。

---

## 4. 补充用例（跨平台能力）

### 4.1 解析 / 下载交互

| ID | 场景 | 步骤 | 期望 |
|----|------|------|------|
| SD-B1 | 空输入 | 不填 URL | 「解析」禁用 |
| SD-B2 | 无效链接 | `https://example.com/not-a-video` | 解析失败提示；无假成功结果区 |
| SD-B3 | 格式切换 | YouTube 等多 format 时改选另一清晰度再下 | 第二次下载成功；文件与所选相关 |
| SD-B4 | 下载中态 | 任一下载进行中 | 按钮禁用 +「下载中…」；不可连点双份 |
| SD-B5 | 缩略图代理 | 抖音 / B 站等有封面链 | 封面显示或失败不影响下载按钮 |
| SD-B6 | 无协议粘贴 | 抖音分享文案里的 `v.douyin.com/...`（无 https） | 能解析（`normalize_media_url`） |

### 4.2 域名变体（各抽 1 条）

| ID | 场景 | 期望 |
|----|------|------|
| SD-V1 | YouTube `youtu.be/...` | 与 watch 链同等可解析下载 |
| SD-V2 | Bilibili `b23.tv/...` | 跳转后可解析下载 |
| SD-V3 | X 用 `twitter.com/.../status/...` | 与 x.com 同等可下 |
| SD-V4 | Instagram `/reel/` 或 `/p/`（含视频） | 至少一种 Pass |

### 4.3 AI 总结 / 字幕 / 导图 / 问答（须测）

> 依赖 LLM（及必要时 ASR）。测前确认 Key 与额度；**不得**因「可选」跳过。  
> 建议在 **同一条 YouTube（或 B 站）已解析成功的页面**上连续跑完本表，避免重复解析耗时。

| ID | 场景 | 步骤 | 期望 |
|----|------|------|------|
| SD-AI-01 | 总结摘要 | 点「AI 总结」→ 等「总结摘要」 | 非空总结正文；流式过程不卡死；有额度提示时数字合理 |
| SD-AI-02 | 字幕文本 | 切到「字幕文本」 | 分句列表条数 ≥ 1，或占位/失败说明可读；来源提示（人工/自动/平台/ASR）可有可无 |
| SD-AI-03 | 思维导图 | 切到「思维导图」；等待生成 | 导图可见（或 Markdown 源可见）；非空白 |
| SD-AI-04 | AI 问答 | 切到「AI 问答」；问「这个视频讲了什么？」 | 非空回答 |
| SD-AI-05 | 字幕导出 | 在字幕 Tab 分别试 SRT / VTT / TXT | **至少一种**触发浏览器下载且文件非空；三种都试更佳 |
| SD-AI-06 | 导图导出 | 导图生成后点 PNG 与/或 SVG | **至少一种**导出成功 |

**跨平台抽测（下载已 Pass 后，各平台点一次「AI 总结」看链路是否通）：**

| ID | 平台 | 期望 |
|----|------|------|
| SD-AI-DY | 抖音 | 总结有正文，或 ASR/占位失败有明确文案（不静默挂起） |
| SD-AI-YT | YouTube | 与 SD-AI-01 可合并；须有正文 |
| SD-AI-BL | Bilibili | 总结有正文（常有平台字幕） |
| SD-AI-X | X | 总结有正文或可解释失败 |
| SD-AI-IG | Instagram | 同上 |
| SD-AI-TT | TikTok | 同上 |
| SD-AI-FB | Facebook | 同上 |

> 全套 Tab（字幕导出 / 导图 / 问答）以 **SD-AI-01～06** 在 YT 或 BL 上跑通为准；其余平台至少 **触发一次总结**（上表 SD-AI-* 平台行），避免每平台重复导图导出。

---

## 5. 平台与 AI 依赖、失败归因

| 平台 / 能力 | 常见依赖 | 典型失败表现 | 处理 |
|-------------|----------|--------------|------|
| 抖音 | 国内可达 iesdouyin；一般无 Cookie | 解析空响应 / 风控 | 换公开链；查网络 |
| TikTok | `HTTP(S)_PROXY`；**HK 出口无效**；`SCRAPECREATORS_API_KEY`（单链主路径） | 超时、空 formats、`/hk/about`、Unexpected response | 配 ScrapeCreators；或换非 HK 代理后再试 yt-dlp |
| YouTube | `YOUTUBE_COOKIEFILE`、代理、Node + `yt-dlp-ejs` | bot 确认、format unavailable | 换 Cookie；确认 JS runtime |
| Bilibili | `BILIBILI_COOKIEFILE`；海外要代理 | HTTP 412、geo-restricted | 导出登录 Cookie；配大陆出口代理 |
| X | 代理；偶发 Cookie | 无视频流、登录墙 | 换公开视频帖；代理 |
| Instagram | `INSTAGRAM_COOKIEFILE` 或浏览器 Cookie | login required、仅图 | 公开 Reel + Cookie |
| Facebook | 代理 / Cookie | Cannot parse、私密 | 换公开视频；代理 |
| AI 总结 / 问答 / 导图 | LLM API Key；登录门闸（非 LOCAL 时） | 空响应、401、额度用尽 | 配 Key；登录；查额度文案 |
| 无字幕转写 | 阿里云 ASR 等；公网可达媒体 URL | 仅占位、转写超时 | 配 ASR；或换有平台字幕的 YT/BL |

**断言原则：** 环境/依赖导致的失败记 **Fail + 备注**，修好依赖后复测；不要把「未配 Cookie / 未配 LLM」标成产品 Pass。

---

## 6. API 对照（手工或 curl）

| 步骤 | 方法 | 说明 |
|------|------|------|
| 健康 | `GET /api/health` | `status=ok` |
| 解析 | `POST /api/parse` body `{"url":"..."}` | `success=true`，`data.title` / `data.formats` |
| 下载 | `POST /api/download` body `{"url":"...","format_id":"..."}` | 返回文件流 |
| 封面 | `GET /api/proxy/thumbnail?url=...` | 200 图片 |
| 总结 | 前端 `summarizeVideo` / 对应 summarize SSE | 流式摘要 + 字幕 + 导图事件 |

抖音走 `douyin` 模块；TikTok 走 `tiktok`（ScrapeCreators）；其余走 `downloader`（yt-dlp）。

---

## 7. 执行记录表

> 执行：API 脚本 `backend/scripts/smoke_single_download.py --min`（对齐最低集；UI 下载中态 / PNG·SVG 导出走备注）  
> 环境：`http://127.0.0.1:8001` LOCAL_MODE=1 + 代理 `127.0.0.1:7897`（出口 HK）　日期：2026-07-10　执行人：agent  
> LLM / ASR：已配置 ☑（`SUMMARIZE_LLM_*`；`ALIYUN_ASR_ENABLED=0`）　未配置（不得 Pass AI）□  
> 日志：`docs/_smoke_single_min_run.log`；FB 复测：`docs/_smoke_single_fb_tt_retry.log`；TT 复测：`docs/_smoke_tt_retry.log`

### 7.1 下载 · 平台

| ID | 执行人 | 日期 | 结果 (Pass/Fail/Skip) | 文件大小/备注 |
|----|--------|------|----------------------|---------------|
| SD-DY-01 | agent | 2026-07-10 | Pass | `v.douyin.com/se8AqLHuAeE`；~50MB |
| SD-TT-01 | agent | 2026-07-10 | Pass | `tiktok.com/@astro_alexandra/video/7513700323718909226`；ScrapeCreators 回退；~10.3MB；复测见 `docs/_smoke_tt_retry.log` |
| SD-YT-01 | agent | 2026-07-10 | Pass | `watch?v=jNQXAC9IVRw`；~0.6MB |
| SD-BL-01 | agent | 2026-07-10 | Pass | `BV1GJ411x7h7`；~34MB |
| SD-X-01 | agent | 2026-07-10 | Pass | `x.com/NASA/status/1527672283828985856`；~15MB |
| SD-IG-01 | agent | 2026-07-10 | Pass | `instagram.com/reel/Dadg8BUqIh0/`；~1.3MB |
| SD-FB-01 | agent | 2026-07-10 | Pass | 初测因 Windows 非法文件名 Fail；`outtmpl=%(id)s` 修复后复测 Pass ~15.6MB |

### 7.2 交互 / 域名

| ID | 执行人 | 日期 | 结果 | 备注 |
|----|--------|------|------|------|
| SD-B1 | agent | 2026-07-10 | Pass | 空 URL → HTTP 400（UI 禁用需浏览器） |
| SD-B2 | agent | 2026-07-10 | Pass | `example.com/not-a-video` 明确失败 |
| SD-B3 | agent | 2026-07-10 | Pass | YT 二次下载成功（该链仅 1 format） |
| SD-B4 | agent | 2026-07-10 | Pass | API 下载完成可观测；按钮「下载中…」需浏览器 |
| SD-B5 | agent | 2026-07-10 | Pass | 抖音封面 proxy 200 webp |
| SD-B6 | agent | 2026-07-10 | Pass | `v.douyin.com/...` 无协议可解析 |
| SD-V1 | agent | 2026-07-10 | Pass | `youtu.be/jNQXAC9IVRw` |
| SD-V2 | agent | 2026-07-10 | Pass | `b23.tv/BV1GJ411x7h7` |
| SD-V3 | agent | 2026-07-10 | Pass | `twitter.com/.../status/...` |
| SD-V4 | agent | 2026-07-10 | Pass | `/reel/` 与 `/p/` 同 shortcode 均可 |

### 7.3 AI（须测）

| ID | 执行人 | 日期 | 结果 | 备注（载体 URL 形态） |
|----|--------|------|------|----------------------|
| SD-AI-01 | agent | 2026-07-10 | Pass | YT `jNQXAC9IVRw`；summary_len≈291 |
| SD-AI-02 | agent | 2026-07-10 | Pass | segments=6 |
| SD-AI-03 | agent | 2026-07-10 | Pass | mindmap markdown 非空 |
| SD-AI-04 | agent | 2026-07-10 | Pass | 问答非空 |
| SD-AI-05 | agent | 2026-07-10 | Pass | 导出 SRT（与前端同逻辑） |
| SD-AI-06 | agent | 2026-07-10 | Pass | API mindmap 源非空；PNG/SVG 需浏览器 |
| SD-AI-DY | agent | 2026-07-10 | Pass | 有总结正文 |
| SD-AI-YT | agent | 2026-07-10 | Pass | 与 01 合并 |
| SD-AI-BL | agent | 2026-07-10 | Pass | 有总结正文 |
| SD-AI-X | agent | 2026-07-10 | Pass | 有总结正文 |
| SD-AI-IG | agent | 2026-07-10 | Pass | 无字幕可解释失败（ASR 关） |
| SD-AI-TT | agent | 2026-07-10 | Pass | 无字幕可解释失败（ASR 关）；解析层已 Pass（ScrapeCreators） |
| SD-AI-FB | agent | 2026-07-10 | Pass | 无字幕可解释失败（ASR 关） |

**出门最低集勾选：**

- [x] 七平台下载：SD-DY-01 … SD-FB-01（含 TT，已 Pass）
- [x] 交互：SD-B1～SD-B6
- [x] 域名变体：SD-V1～SD-V4
- [x] AI 全套：SD-AI-01～SD-AI-06
- [x] 各平台至少一次 AI 总结：SD-AI-DY / YT / BL / X / IG / TT / FB

**结论（2026-07-10）：** 最低集 **30/30 Pass**。TikTok 因代理出口香港被重定向到 `/hk/about`，已改为 ScrapeCreators 取 CDN 直链；FB 文件名问题此前已修。

---

## 8. 当前问题汇总（2026-07-10）

> 依据交付机 API 冒烟（`smoke_single_download.py --min` + FB / TT 复测）。状态随复测更新。

### 8.1 阻塞（出门验收未满）

| # | 问题 | 影响 | 现象 / 证据 | 建议处理 |
|---|------|------|-------------|----------|
| — | （无） | — | 七平台下载已齐 | — |

### 8.2 环境 / 配置缺口（非产品逻辑坏，但影响体验）

| # | 问题 | 影响 | 现象 / 证据 | 建议处理 |
|---|------|------|-------------|----------|
| E1 | **ASR 关闭**（`ALIYUN_ASR_ENABLED=0`） | IG / FB（及无平台字幕的链）无法出 AI 总结正文 | 冒烟返回明确文案：「该视频没有可用的字幕，无法生成总结」；记 Pass（可解释），但本机生产若要「弱字幕也能总结」须开 ASR | 需要时开 ASR 并保证媒体 URL 公网可达；或仅用有字幕的 YT/BL 做 AI |
| E2 | **浏览器 UI 细项未手工验收** | 「下载中…」按钮态、导图 **PNG/SVG** 导出 | API 侧下载与 mindmap Markdown 已过；文档要求的浏览器态未点验 | 浏览器对任意一条已解析页点一次下载中态 + 导图导出即可补齐 |
| E3 | **代理出口香港** | 纯 yt-dlp 抓 TikTok 网页仍会失败 | `ipinfo` → HK；请求被重定向到 `tiktok.com/hk/about` | 单链已走 ScrapeCreators；若坚持 yt-dlp 需换 **非 HK** 出口（如 US） |

### 8.3 已修复（勿再当未修缺陷）

| # | 问题 | 修复 |
|---|------|------|
| F1 | Facebook 下载 Windows `Errno 22`（标题含 NBSP 等非法文件名字符） | `downloader` 改为 `outtmpl=%(id)s` + `windowsfilenames`；文件名清洗加强；`SD-FB-01` 复测 Pass |
| F2 | TikTok 单链解析/下载失败（`Unexpected response from webpage request`） | 根因：代理出口 HK → `/hk/about`；新增 `tiktok.py`（ScrapeCreators `/v2/tiktok/video` → CDN 直链）；`main` / 批量下载路由；勿把 B 站 cookies 套到 TikTok；`SD-TT-01` 复测 Pass ~10.3MB |

### 8.4 能力边界（设计如此，非本次回归）

- 单链**无**「跳过已下载 / 打包 ZIP」（在关键词与批量流程）。
- 抖音 ≠ TikTok：抖音专用模块；TikTok 优先 ScrapeCreators（HK 出口下 yt-dlp 网页不可用），不可互相替代验收。
- 图文帖 / 纯音频 / 直播回放可能无合适 video format。
- Cookie 过期会导致 YT / BL / IG 突然 Fail（运维换 Cookie）。
- 样例 URL 易失效；复测须换公开可播链。
- TikTok 单链依赖 `SCRAPECREATORS_API_KEY` 额度；额度不足会明确报错。

**一句话：** 单链七平台下载与 AI 主链路可用；TikTok 在 HK 代理下走 ScrapeCreators；弱字幕 AI 与 UI 导图导出属配置/补验项。

---

## 9. 已知限制（长期）

- 单链下载**无**「跳过已下载 / 打包 ZIP」；该能力在关键词与批量流程。
- 抖音与 TikTok 链路不同：抖音专用解析；TikTok 优先 ScrapeCreators（HK 等受限出口下 yt-dlp 网页不可用），不可互相替代验收。
- 图文帖 / 纯音频 / 直播回放可能无合适 video format，选「公开短视频 / Reel」测。
- Cookie 过期会导致 YT/BL/IG 突然 Fail，属运维问题；换 Cookie 后复测。
- AI 受额度、登录门闸、ASR 公网可达性影响；**冒烟前须配好 LLM**，缺配置记 Fail 而非 Skip。
- 弱字幕平台（部分抖音 / X）允许「总结成功但字幕为占位 + 说明」；导图/问答仍须在 YT/BL 载体上严格 Pass。
- 样例 URL 易失效；执行记录须写清实际使用的链接形态（可打码 ID）。
