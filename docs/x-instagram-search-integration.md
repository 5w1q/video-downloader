# X（Twitter）与 Instagram 关键词搜索接入说明

> 状态：X / Instagram 均已实现（Apify）  
> 日期：2026-07-14（IG 搜索层由 ScrapeCreators 换为 Apify）  
> 目标：对齐现有 YouTube「关键词搜索 → 互动/日期筛选 → 列表 → 下载」能力  
> 范围：仅 **X**、**Instagram**；不做「收藏」筛选  
> X 额外硬需求：转发 ≥ N、评论（回复）≥ N  
> IG 额外硬需求：评论 ≥ N  
> **本机生产完善**：须遵守 [`local-production-base-rules.md`](./local-production-base-rules.md) §2/§4；验收用 `smoke-x-keyword-download.md` / `smoke-instagram-keyword-download.md`；目标机依赖见基线 §3（`APIFY_TOKEN` + 代理；TikTok 单链仍可选 `SCRAPECREATORS_API_KEY`）。

---

## 1. 产品需求（已确认）

| 能力 | 是否需要 | 说明 |
|------|----------|------|
| 关键词搜索 | ✅ | 与 YouTube 一致 |
| 点赞 ≥ N | ✅ | 硬需求（X / IG） |
| 转发 ≥ N | ✅（仅 X） | 硬需求；服务端 `min_retweets:N` |
| 评论 ≥ N | ✅ | 硬需求；X = 回复 `min_replies`；IG = `comment_count` 本地过滤 |
| 日期筛选 | ✅ | 硬需求 |
| 收藏 ≥ N | ❌ | 平台公开能力不足，明确不做 |
| 下载 | ✅ | 搜索拿到 URL 后复用现有 yt-dlp / bulk 流水线 |

可选增强（非必须）：

- **X**：仅视频（`filter:videos`，下载场景建议默认开启）
- **IG**：播放量 ≥ N（返回字段有，本地过滤即可）

---

## 2. 总体架构（对齐 YouTube）

```
前端筛选项
  → POST /api/{platform}/search
  → 搜索层（第三方 SaaS，不自养 Cookie）
  → 统一结果列表（id/title/url/likes/date/...）
  → 用户勾选 / 或 search-download
  → 现有 bulk_download_core + yt-dlp 下载
```

**原则：**

1. **搜索层用托管 API**（生产不自建 twscrape / instagrapi 主路径）
2. **下载层继续 yt-dlp**（单条 URL；Cookie 仅用于下载风控，不用于搜索）
3. API / 前端交互尽量复用 `youtube_search` / `YoutubeSearchSection` 形态

参考现有实现：

- 后端：`backend/youtube_search.py`、`backend/api_youtube_search.py`
- 前端：`frontend/src/api/youtube.js`、`frontend/src/components/YoutubeSearchSection.vue`

---

## 3. 供应商选型（结论）

| 平台 | 推荐供应商 | 端点 / Actor | 定价量级（调研时） |
|------|------------|--------------|-------------------|
| **X** | Apify | [api-ninja/x-twitter-advanced-search](https://apify.com/api-ninja/x-twitter-advanced-search) | 约 $0.35 / 1,000 results |
| **X 备选** | Apify | [mikolabs/x-twitter-advanced-search-tweet-scraper](https://apify.com/mikolabs/x-twitter-advanced-search-tweet-scraper) | 按 Actor 定价 |
| **Instagram** | Apify | [data-slayer/instagram-search-reels](https://apify.com/data-slayer/instagram-search-reels) | 约 $1.50–$2.50 / 1,000 results（Pay-per-event） |

### 不推荐作为主方案

| 方案 | 原因 |
|------|------|
| EnsembleData（X） | 文档仅有 user/tweets、post/info，**无关键词搜索端点** |
| 官方 X API | 能搜但贵；`min_faves` 体验不如 Apify Advanced Search 直观 |
| Meta Graph API（IG） | 审核/权限，不适合「关键词捞视频」 |
| twscrape / instagrapi 自建 | 养号、封禁、GraphQL 易挂，不适合生产主路径 |
| ScrapeCreators（X） | X 侧无真正关键词搜索端点（仅 profile/tweet 等） |

---

## 4. X（Twitter）接入规格

### 4.1 能力矩阵

| 能力 | 支持方式 | 备注 |
|------|----------|------|
| 关键词 | query 文本 | ✅ |
| 点赞 ≥ | 服务端 `min_faves:N` | ✅ 硬需求，优先服务端筛 |
| 转发 ≥ | 服务端 `min_retweets:N` | ✅ 硬需求，优先服务端筛 |
| 评论（回复）≥ | 服务端 `min_replies:N` | ✅ 硬需求；产品文案用「评论」，运算符为 replies |
| 日期 | `since:YYYY-MM-DD` / `until:YYYY-MM-DD`；或 `within_time:7d` | ✅ 可做区间 |
| 仅视频 | `filter:videos` | ✅ 下载场景建议默认带上 |
| 播放量 ≥ | 无稳定公开运算符 | 不做硬筛；有字段则仅展示 |
| 收藏 ≥ | 不支持 | 明确不做 |

### 4.2 推荐查询拼装

```text
{关键词} filter:videos min_faves:{min_likes} min_retweets:{min_retweets} min_replies:{min_comments} since:{date} until:{date+1}
```

规则：

- `min_likes > 0` 时追加 `min_faves:{min_likes}`
- `min_retweets > 0` 时追加 `min_retweets:{min_retweets}`
- `min_comments > 0` 时追加 `min_replies:{min_comments}`（X 的「评论」= 回复数）
- 下载场景默认追加 `filter:videos`
- 日期按前端 `date_filter` / `upload_date` 转成 `since` / `until`（或 `within_time`）

示例：

```text
AI filter:videos min_faves:100 min_retweets:20 min_replies:10 since:2026-06-01 until:2026-07-11
```

### 4.3 Apify 调用要点

- 平台：Apify Actor API（需 `APIFY_TOKEN`）
- 推荐 Actor：`api-ninja/x-twitter-advanced-search`
- 输入形态（示意）：

```json
{
  "query": "AI filter:videos min_faves:100 min_retweets:20 min_replies:10 since:2026-06-01 until:2026-07-11",
  "search_type": "Latest",
  "numberOfTweets": 40
}
```

也可用 Actor 的 structured `advancedFilters`（关键词 / 日期 / engagement：likes+retweets+replies / media），实现时以该 Actor 当前 Input Schema 为准。

### 4.4 建议统一输出字段（对齐 YouTube）

| 字段 | 来源建议 | 说明 |
|------|----------|------|
| `id` | tweet id | |
| `title` | 推文文本截断 | 无标题时用正文前 N 字 |
| `url` | `https://x.com/i/status/{id}` | 给 yt-dlp |
| `uploader` | 用户名 / name | |
| `like_count` | likes / favorite_count | |
| `retweet_count` | retweet / reprint 计数 | X 专用；列表展示与兜底过滤 |
| `comment_count` | reply_count / replies | 产品侧称「评论」；对应 X replies |
| `view_count` | 若有则填，否则 `null` | 不参与硬筛 |
| `upload_date` | 推文创建时间 → `YYYYMMDD` | |
| `thumbnail` | 媒体缩略图（若有） | |
| `duration` / `duration_string` | 若有视频元数据 | 可空 |
| `platform` | `"x"` | 便于前端区分 |

### 4.5 环境变量（建议）

```env
APIFY_TOKEN=...
X_SEARCH_ACTOR_ID=api-ninja/x-twitter-advanced-search
X_SEARCH_MAX_RESULTS=20
X_SEARCH_POOL=40
```

### 4.6 建议后端文件

```
backend/x_search.py          # 拼 query、调 Apify、归一化结果、本地兜底过滤
backend/api_x_search.py      # POST /api/x/search 、可选 /api/x/search-download
```

---

## 5. Instagram 接入规格

### 5.1 能力矩阵

| 能力 | 支持方式 | 备注 |
|------|----------|------|
| 关键词 | Actor 输入 `query` | ✅ |
| 日期 | Actor **无**服务端日期参数 → 用 `taken_at_date` **本地过滤** | ✅ 相对档 + 指定日 |
| 点赞 ≥ | 返回 `like_count` → **本地过滤** | ✅ |
| 评论 ≥ | 返回 `comment_count` → **本地过滤** | ✅ |
| 播放 ≥ | 返回 `ig_play_count` / `play_count` → 本地过滤 | ✅ |
| 转发 ≥ | 无对应产品需求 | IG 不做 |
| 收藏 ≥ | 明确不做 | |

说明：Apify Actor 输入仅 `query` + `maxPages`；互动/日期一律本地筛。无 `video_url` 时下载回退 yt-dlp（建议配 `INSTAGRAM_COOKIEFILE`）。

### 5.2 Apify 调用

- Actor：`data-slayer/instagram-search-reels`（可用 `INSTAGRAM_SEARCH_ACTOR_ID` 覆盖）
- 端点：`POST /v2/acts/{actor}/run-sync-get-dataset-items?token=...`
- 输入示意：

```json
{
  "query": "AI",
  "maxPages": 2
}
```

文档：https://apify.com/data-slayer/instagram-search-reels

### 5.3 响应关键字段

| 字段 | 用途 |
|------|------|
| `code` / `id` | shortcode / media id → 页面 URL |
| `caption.text` | 标题/描述 |
| `like_count` | 点赞筛选 |
| `comment_count` | 评论筛选 |
| `ig_play_count` / `play_count` | 播放筛选 |
| `taken_at_date` | 日期展示与本地过滤 |
| `video_url` | CDN 直下（优先） |
| `video_duration` | 时长 |
| `user.username` | uploader |
| `thumbnail_url` | 封面 |

### 5.4 日期与前端映射

| 前端选项 | 本地过滤 |
|----------|----------|
| 全部 | 不滤日期 |
| 近 24 小时 | `upload_date == 今日` |
| 近一周 / 近一月 / 近一年 | `since ≤ upload_date < until` |
| 指定日期 | `upload_date == 所选日` |

### 5.5 统一输出字段

| 字段 | 来源 |
|------|------|
| `id` | `id` 或 `code` |
| `title` | `caption.text` 截断 |
| `url` | `https://www.instagram.com/reel/{code}/` |
| `video_url` | `video_url`（可空） |
| `uploader` | `user.username` |
| `like_count` / `comment_count` / `view_count` | 对应字段 |
| `upload_date` | `taken_at_date` → `YYYYMMDD` |
| `platform` | `"instagram"` |

### 5.6 环境变量

```env
APIFY_TOKEN=...
INSTAGRAM_SEARCH_ACTOR_ID=data-slayer/instagram-search-reels
INSTAGRAM_SEARCH_MAX_RESULTS=20
INSTAGRAM_SEARCH_POOL=40
```

### 5.7 后端文件

```
backend/instagram_search.py
backend/api_instagram_search.py   # POST /api/instagram/search 、 search-download
```

---

## 6. 建议对外 API 形态（与 YouTube 对齐）

### 6.1 仅搜索

```http
POST /api/x/search
POST /api/instagram/search
Content-Type: application/json
```

```json
{
  "query": "关键词",
  "max_results": 20,
  "min_likes": 100,
  "min_retweets": 20,
  "min_comments": 10,
  "min_views": 0,
  "date_filter": "all|today|week|month|date",
  "upload_date": "YYYY-MM-DD"
}
```

说明：

- **X**：`min_likes` → `min_faves`；`min_retweets` → `min_retweets`；`min_comments` → `min_replies`（均为服务端筛选）；`min_views` 可忽略或仅本地弱过滤；`min_retweets` 仅 X 使用
- **IG**：`min_likes` / `min_comments` / `min_views` / `date_filter` → 全部本地过滤（Apify 无服务端筛选项）；忽略 `min_retweets`

### 6.2 搜索并下载（可选二期）

```http
POST /api/x/search-download
POST /api/instagram/search-download
```

复用 `bulk_download_core` + SSE，与 `/api/youtube/search-download` 同模式。

### 6.3 统一响应示意

```json
{
  "query": "...",
  "platform": "x",
  "count": 12,
  "items": [
    {
      "id": "...",
      "title": "...",
      "url": "https://...",
      "uploader": "...",
      "like_count": 1234,
      "retweet_count": 56,
      "comment_count": 10,
      "view_count": null,
      "upload_date": "20260701",
      "upload_date_display": "2026-07-01",
      "thumbnail": "...",
      "duration": null,
      "duration_string": "",
      "below_threshold": false,
      "platform": "x"
    }
  ]
}
```

---

## 7. 前端建议

| 组件 | 说明 |
|------|------|
| `XSearchSection.vue` | 筛选项：关键词、点赞 ≥、转发 ≥、评论 ≥、日期 |
| `InstagramSearchSection.vue` | 筛选项：关键词、点赞 ≥、评论 ≥、播放（可选）、日期档（无转发） |
| `frontend/src/api/x.js` / `instagram.js` | 对齐 `youtube.js`；两边都传 `min_comments`；X 另传 `min_retweets` |

UI 可与 `YoutubeSearchSection.vue` 共用布局模式；平台差异用文案区分（例如 IG 日期为「近一周」档位）。

---

## 8. 实现顺序建议

1. **先做 X + Apify**（服务端点赞/转发/评论/日期最接近产品需求）
2. **再做 IG + Apify**（`data-slayer/instagram-search-reels`；本地过滤点赞/评论/播放/日期）
3. 两边都通后再接 `search-download` SSE
4. 开源自建（twscrape / instagrapi）仅作灾备，不进默认路径

### POC 验收清单

- [x] X：关键词 + `min_faves` + `min_retweets` + `min_replies` + 日期区间 → 返回可点开的 `x.com/status/...`
- [x] X：结果含 `like_count` / `retweet_count` / `comment_count`，且低于阈值的条目被滤掉
- [ ] X：结果能被现有 yt-dlp 下载（必要时 Cookie）
- [x] IG：关键词（Apify Reels Search）→ 返回 reel URL + 可选 `video_url`
- [x] IG：本地 `like_count` / `comment_count` / `ig_play_count` + 日期过滤正确
- [ ] IG：CDN `video_url` 或 yt-dlp 可下载（无直链时建议 Cookie）
- [x] 失败时错误信息可读（额度不足 / Actor 失败 / 无结果）

---

## 9. 风险与合规

1. 均为**非官方**数据源，需法务评估 ToS / 商用合规
2. Apify Actor 上游变更会导致字段或成功率波动，需监控失败率
3. IG 日期为本地过滤：召回池偏「相关/热门」时，近 24 小时等档可能更易空
4. X/IG 下载仍可能触发风控，下载 Cookie 与搜索 API Token 分开管理
5. 密钥只放环境变量 / secrets，勿写入仓库

---

## 10. 关键链接

| 资源 | URL |
|------|-----|
| Apify X Advanced Search（推荐） | https://apify.com/api-ninja/x-twitter-advanced-search |
| Apify X Advanced Search（备选） | https://apify.com/mikolabs/x-twitter-advanced-search-tweet-scraper |
| Apify IG Reels Keyword Search | https://apify.com/data-slayer/instagram-search-reels |
| X 高级搜索运算符参考 | https://github.com/igorbrigadir/twitter-advanced-search |
| 现有 YouTube 搜索 | `backend/youtube_search.py` |

---

## 11. 一句话结论

- **X**：用 **Apify Advanced Search**，服务端做关键词 + 点赞（`min_faves`）+ 转发（`min_retweets`）+ 评论/回复（`min_replies`）+ 日期（+ 仅视频）  
- **Instagram**：用 **Apify Reels Keyword Search**（`data-slayer/instagram-search-reels`），请求做关键词；本地做点赞 + 评论 + 播放 + 日期；无转发  
- **收藏**：两边都不做  
- **下载**：优先 CDN `video_url`，否则 yt-dlp / bulk 流水线
