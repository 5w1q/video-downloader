# 统一视频命名方案

> 日期：2026-07-14  
> 状态：规范已冻结；**YouTube / X / Instagram / 表格批量命名已接入**（§4.1–§4.3 + 通用 bulk）  
> 目标：以单链下载的内容标题规则为**唯一标准**，对齐 YouTube / X / Instagram 关键词批量与通用批量下载的最终落盘文件名  
> 范围：服务端最终文件名（浏览器 `Content-Disposition` / SSE `filename` / ZIP 包内成员名）；不改 ZIP 包名、不新增前端自定义文件名 UI  
> 相关实现：`backend/video_title.py`、`backend/downloader.py`、`backend/bulk_download_core.py`、`backend/douyin.py`、`backend/tiktok.py`、`backend/api_youtube_search.py`、`backend/api_x_search.py`、`backend/api_instagram_search.py`  
> 本机生产完善顺序：[`local-production-base-rules.md`](./local-production-base-rules.md) §4 → 各平台 smoke → 本文 §5 命名专项验收

---

## 1. 命名规范（冻结）

所有下载入口最终对外暴露的文件名，须符合本节。核心模块：[`backend/video_title.py`](../backend/video_title.py)。

### 1.1 最终格式

```
{sanitize 后短标题 ≤ 30 字}.{ext}
```

| 规则 | 行为 |
|------|------|
| 开关 | `CONTENT_TITLE_ON_DOWNLOAD`（默认 `1` / 开启；`0` / `false` / `off` 关闭） |
| 主干长度 | `MAX_TITLE_LEN = 30` |
| 扩展名 | 取自已下载文件（如 `mp4`、`webm`、`mp3`） |
| 不含 | 日期、平台名前缀、uploader、upload_date（除非平台标题原文自带） |
| 重名 | 同目录追加 `_2`、`_3`…（尽量保持主干 ≤ 30） |
| 空/无效回退 | `video.{ext}` |

### 1.2 标题生成优先级

`generate_download_title(url, platform_title=...)`：

1. 对 `url` 抽取字幕 / 转写（`SubtitleExtractor`）
2. 有字幕且已配置 LLM（`DEEPSEEK_API_KEY` / `SUMMARIZE_LLM_API_KEY`）→ `generate_short_title`（≤ 30 字）再 sanitize
3. 有字幕但无 LLM / LLM 失败 → 字幕正文 sanitize 截断
4. 无字幕 → sanitize(`platform_title`)
5. 仍为空 → `video`

关闭内容标题时（`CONTENT_TITLE_ON_DOWNLOAD=0`）：**直接** sanitize(`platform_title`)，不再抽字幕 / 调 LLM。规范要求此时对外 filename 仍为「平台标题清洗后 ≤ 30 字」，**不得**长期以裸 `{id}.{ext}` 作为对外名（见 §4.4）。

### 1.3 清洗规则（`sanitize_download_basename`）

- 去掉 `\ / * ? : " < > | # @`、控制字符、NBSP / 全角空格
- **含中文时繁体 → 简体**（`zhconv`，覆盖 YouTube / X / Instagram / 表格批量等所有入口）
- 折叠连续空格与下划线
- 去掉首尾装饰引号 / 书名号 / 括号
- 截断至 `max_len`（默认 30）

弱标题检测（`looks_like_weak_title`）：纯 ID 样、长数字串等，更应依赖字幕 / 显式传入的搜索 caption。

### 1.4 下载中间态（实现细节，非最终对外名）

yt-dlp 路径（`downloader.py`）先用：

```text
outtmpl = %(id)s.%(ext)s
```

避免 Windows 非法字符（Errno 22）。下载完成后由 `apply_content_filename` rename 为 §1.1 格式。

抖音 / TikTok：先按平台 desc/title 落盘，再同样走 `apply_content_filename`。

### 1.5 调用链

```
单链 POST /api/download
  └─ douyin / tiktok / downloader.download_video
       └─ video_title.apply_content_filename
            └─ 最终 filename → FileResponse / return_json

批量 download_one（Excel / YT / X / IG / URL 列表）
  └─ 同上（Douyin/TikTok 专用模块，其余 yt-dlp）
       └─ apply_content_filename
            └─ SSE filename / ZIP 成员名 / 单文件投递名
```

**原则：** 只有一套命名实现（`video_title.py`）。入口差异只允许在「传入的下载 URL、字幕用 URL、platform_title」上；禁止各平台私自再写一套最终命名模板。

---

## 2. 入口对照表

| 入口 | API / 脚本 | 下载函数 | 命名路径 | 与规范对齐？ | 备注 |
|------|------------|----------|----------|--------------|------|
| 单链首页 | `POST /api/download` | `downloader` / `douyin` / `tiktok` | `apply_content_filename` | ✅ **标准参照** | 见 [`smoke-single-download.md`](./smoke-single-download.md) |
| Excel / URL 批量 | `api_bulk_download` → bulk | `extract_entries` + `platform_titles` | 同上 | ✅ 已对齐 | CSV/JSON/Excel 表头可带 `title`；预览下载传 titles |
| YouTube 关键词批量 | `api_youtube_search` → bulk | `download_one(页面URL)` + `platform_titles` | 同上 | ✅ 已对齐 | 搜索 `title` 已传入命名回退 |
| X 关键词批量 | `api_x_search` → bulk | `download_one(页面URL)` + `platform_titles` | 同上 | ✅ 已对齐 | 推文截断 `title` 已传入命名回退 |
| Instagram 关键词批量 | `api_instagram_search` → bulk | CDN 下载 + 页面 URL 命名 + `platform_titles` | 同上 | ✅ 已对齐 | caption 作命名回退；skip 仍按页面 URL |
| CLI 队列 | `scripts/bulk_download_queue.py` → `/api/download` | 单链路径 | 同单链 | ✅ | 不经 SSE bulk |
| 抖音 / TikTok（任意入口） | 专用模块 | `apply_content_filename` | 已对齐 | ✅ | 验收异常再单列 |

ZIP **包名**（如 `youtube-batch-part01.zip`、`batch-download.zip`）不在本规范内；仅包内视频文件名须符合 §1。

冒烟文档：

| 入口 | Smoke |
|------|-------|
| 单链 | [`smoke-single-download.md`](./smoke-single-download.md) |
| 通用批量 | [`smoke-bulk-download.md`](./smoke-bulk-download.md) |
| YouTube 关键词 | [`smoke-youtube-keyword-download.md`](./smoke-youtube-keyword-download.md) |
| X 关键词 | [`smoke-x-keyword-download.md`](./smoke-x-keyword-download.md) |
| Instagram 关键词 | [`smoke-instagram-keyword-download.md`](./smoke-instagram-keyword-download.md) |
| YouTube 生产就绪 | [`youtube-keyword-download-production-readiness.md`](./youtube-keyword-download-production-readiness.md) |

---

## 3. 缺口与根因

### 3.1 Instagram 搜索批量（已修复）

**原现象：** 搜索列表 caption 合理，下载后文件名常为弱 ID / `video`。

**原根因：** CDN `video_url` 直下时未传入 caption；字幕对 CDN URL 失败。

**现行为：** `urls` = 页面 URL（skip / 历史 / `title_url`），`download_urls` = CDN，`platform_titles` = 搜索 caption；`apply_content_filename` 以 caption 为命名回退。

### 3.2 YouTube / X 搜索批量

**YouTube / X（已修复）：** 搜索 `title`（YT 视频标题 / X 推文截断）经 `platform_titles` 传入 bulk → `download_video` → `apply_content_filename`；弱字幕时回退接近列表标题 ≤ 30 字。

### 3.3 元数据通道（已补齐）

`run_bulk_download_stream` 现支持：

- `urls`：跳过 / 历史 / 命名用页面 URL（`title_url`）
- `download_urls`：可选，实际下载地址（IG CDN）
- `platform_titles`：可选，与 `urls` 等长的搜索标题回退

Excel / 纯 URL 批量：`extract_entries_from_upload` 识别可选 `title`/`caption` 列与 `video_url`；预览勾选下载经 `/bulk-download/urls` 传 `titles`（IG 另传 `download_urls`）。无标题时仍依赖 yt-dlp / 字幕路径。
### 3.4 关闭开关时行为不统一（次要）

- 内容标题开启时：最终 ≤ 30 字内容名。
- 关闭时：yt-dlp 盘上可能仍是 `{id}.{ext}`，返回 filename 与抖音/TikTok 的「平台标题 sanitize」不完全一致；`downloader._sanitize_filename` 截断 **180** 字，与最终规范 **30** 字也不一致。

---

## 4. 改造清单（按优先级）

后续改代码时按序勾选；**本文件为计划与规范，不代替 PR 说明。**

### 4.1 扩展批量元数据通道

- [x] `download_one(url, format_id, output_dir, *, platform_title="", title_url="")`  
  - `url`：实际下载地址（可为 CDN）  
  - `title_url`：字幕 / AI 用（默认 = 页面 URL 或 `url`）  
  - `platform_title`：搜索 / 解析侧已知标题
- [x] `run_bulk_download_stream(..., platform_titles=None)`：与 `urls` 等长；传给 `download_one`
- [x] `downloader.download_video(..., platform_title=None, title_url=None)`：rename 时优先用显式 title；`apply_content_filename` 的第二参用 `title_url or url`
- [x] 抖音 / TikTok：若调用方传入 `platform_title`，显式非空优先于平台 desc/title

### 4.2 Instagram 对齐（最高优先）

- [x] [`api_instagram_search.py`](../backend/api_instagram_search.py)：构造 `platform_titles`（来自搜索 `title` / caption）
- [x] 调用 bulk 时：`urls` = 页面 URL，`download_urls` = CDN，`platform_titles` = caption
- [x] `download_one` / `download_video`：下载 CDN，命名用页面 URL + caption（§4.1 已具备）
- [ ] 回归：[`smoke-instagram-keyword-download.md`](./smoke-instagram-keyword-download.md) + 本文 §5.3

### 4.3 YouTube / X 对齐

- [x] [`api_youtube_search.py`](../backend/api_youtube_search.py)：搜索结果 `title` → `platform_titles`
- [x] [`api_x_search.py`](../backend/api_x_search.py)：推文截断 `title` → `platform_titles`
- [x] 无字幕 / LLM 失败时，最终文件名应接近搜索列表标题的 sanitize ≤ 30 字版本（YouTube / X 已接）
- [ ] 回归：[`smoke-youtube-keyword-download.md`](./smoke-youtube-keyword-download.md)、[`smoke-x-keyword-download.md`](./smoke-x-keyword-download.md) + 本文 §5.2

### 4.4 统一关闭开关行为

- [x] `CONTENT_TITLE_ON_DOWNLOAD=0`：yt-dlp 路径（含 YouTube）对外 filename = `sanitize_download_basename(platform_title)` + ext（≤ 30）（`download_video` 始终调用 `apply_content_filename`）
- [x] 不再把裸 `{id}.{ext}` 作为 API / SSE 返回的最终 `filename`（盘上可短暂用 id，返回前 rename）—— yt-dlp 路径已对齐
- [ ] 文档 / `.env.example` 注释与行为一致
- [ ] 抖音 / TikTok 关闭开关时行为复核

### 4.5 回归与文档

- [ ] 跑通本文 §5 命名专项用例
- [ ] 若 smoke 文档需增「文件名符合内容标题」勾选项，同步改对应 smoke（可选）
- [ ] 更新 [`local-production-base-rules.md`](./local-production-base-rules.md) 基线状态（若该能力列入基线）

---

## 5. 验收用例（命名专项）

通用通过标准（在对应 smoke 通过的前提下追加）：

- 最终文件名主干 ≤ 30 字（不含扩展名；重名后缀 `_2` 等除外）
- 不含 Windows 非法字符 `\ / * ? : " < > |`
- 非空、非纯 CDN 哈希 / 纯数字短码（在能提供 caption 或平台标题时）
- `CONTENT_TITLE_ON_DOWNLOAD=1` 且 LLM+字幕可用时：可读短标题（可与原文不同，须像「内容概括」）

### 5.1 单链（参照基线）

前置：[`smoke-single-download.md`](./smoke-single-download.md) 下载 Pass。

| ID | 步骤 | 期望 |
|----|------|------|
| VN-SL-01 | YouTube 公开有字幕链，单链下载 | 文件名 ≤ 30 字内容标题；非 `{video_id}.mp4` |
| VN-SL-02 | 抖音短链下载 | 走 `apply_content_filename`；非空可读名 |
| VN-SL-03 | `CONTENT_TITLE_ON_DOWNLOAD=0` 后重启，再下同一链 | 文件名为平台标题 sanitize ≤ 30；仍非裸 id（改造 §4.4 后） |

### 5.2 YouTube / X 关键词批量

前置：[`smoke-youtube-keyword-download.md`](./smoke-youtube-keyword-download.md)、[`smoke-x-keyword-download.md`](./smoke-x-keyword-download.md)。

| ID | 步骤 | 期望 |
|----|------|------|
| VN-YT-01 | 关键词搜 1～2 条并下载 | 包内 / 单文件名符合 §1；弱字幕时接近搜索列表 title 截断 |
| VN-X-01 | 同上（视频帖） | 同左；回退可用推文截断 title |

### 5.3 Instagram 关键词批量（改造后必测）

前置：[`smoke-instagram-keyword-download.md`](./smoke-instagram-keyword-download.md)。

| ID | 步骤 | 期望 |
|----|------|------|
| VN-IG-01 | 关键词搜出带 caption 的条目并下载 | 文件名接近 caption sanitize ≤ 30；**不得**长期为 CDN/ID/`video`（无 caption 时可例外并备注） |
| VN-IG-02 | 确认 skip 历史仍按**页面 URL** | 重复下载可 skip；命名不影响 skip 键 |

### 5.4 通用批量

前置：[`smoke-bulk-download.md`](./smoke-bulk-download.md)。

| ID | 步骤 | 期望 |
|----|------|------|
| VN-BK-01 | Excel/URL 列表含 YT+X 各 1 条 | 各文件均符合 §1；ZIP 成员名与磁盘 basename 一致（重名仅 `_N` 差异） |

---

## 6. 明确不改（本阶段）

| 项 | 说明 |
|----|------|
| 前端自定义文件名输入 | 单链亦无此 UI；命名全在服务端 |
| ZIP 包名 | 如 `instagram-batch.zip`、`youtube-batch-part01.zip` 保持现状 |
| 日期 / 平台前缀模板 | 不引入 `{date}_{platform}_{title}` 等第二套模板，除非产品另行立项 |
| Douyin/TikTok 专项重写 | 已走 `apply_content_filename`；仅验收失败时再开单 |

---

## 7. 关键文件索引

| 文件 | 角色 |
|------|------|
| [`backend/video_title.py`](../backend/video_title.py) | 唯一最终命名实现 |
| [`backend/downloader.py`](../backend/downloader.py) | yt-dlp 下载 + 调用 rename |
| [`backend/bulk_download_core.py`](../backend/bulk_download_core.py) | `download_one` / `run_bulk_download_stream` |
| [`backend/api_instagram_search.py`](../backend/api_instagram_search.py) | IG CDN + 待传 caption |
| [`backend/api_youtube_search.py`](../backend/api_youtube_search.py) | YT 搜索 → bulk |
| [`backend/api_x_search.py`](../backend/api_x_search.py) | X 搜索 → bulk |
| [`backend/api_bulk_download.py`](../backend/api_bulk_download.py) | Excel/URL 批量 |
| [`backend/douyin.py`](../backend/douyin.py) / [`tiktok.py`](../backend/tiktok.py) | 专用下载 + 已接 rename |
| [`backend/summarizer.py`](../backend/summarizer.py) | 字幕抽取与短标题 LLM |
| [`backend/.env.example`](../backend/.env.example) | `CONTENT_TITLE_ON_DOWNLOAD` |

---

## 8. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-14 | 初版：冻结单链规范、入口对照、缺口、改造 checklist、命名专项验收；交叉引用各 smoke / 集成文档 |
| 2026-07-14 | YouTube：`platform_titles` 贯通 bulk → `download_video`；搜索标题作命名回退；yt-dlp 路径始终 `apply_content_filename` |
| 2026-07-14 | X：`api_x_search` 推文截断 `title` → `platform_titles`，与 YouTube 同通道 |
| 2026-07-14 | Instagram：CDN 仍直下；页面 URL + caption → 命名回退，修复弱 ID/`video` 文件名 |
| 2026-07-14 | 通用批量：表格解析 title/CDN 列；`/bulk-download/urls` 支持 titles/download_urls；预览下载传标题；抖音/TikTok 接受显式 platform_title |
