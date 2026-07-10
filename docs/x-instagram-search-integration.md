# X（Twitter）与 Instagram 关键词搜索接入说明

> 状态：X 已实现（Apify）；Instagram 已实现（ScrapeCreators）  
> 日期：2026-07-10  
> 目标：对齐现有 YouTube「关键词搜索 → 互动/日期筛选 → 列表 → 下载」能力  
> 范围：仅 **X**、**Instagram**；不做「收藏」筛选  
> X 额外硬需求：转发 ≥ N、评论（回复）≥ N  
> IG 额外硬需求：评论 ≥ N  
> **本机生产完善**：须遵守 [`local-production-base-rules.md`](./local-production-base-rules.md) §2/§4；验收用 `smoke-x-keyword-download.md` / `smoke-instagram-keyword-download.md`；目标机依赖见基线 §3（`APIFY_TOKEN` / `SCRAPECREATORS_API_KEY` + 代理）。

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
| **Instagram** | ScrapeCreators | `GET /v2/instagram/reels/search` | 约 $47 / 25k credits 起；credits 不过期 |

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
| 关键词 | `query` | ✅ |
| 日期 | 请求参数 `date_posted` | ✅ 相对档：`last-hour` / `last-day` / `last-week` / `last-month` / `last-year` |
| 点赞 ≥ | 返回 `like_count` → **本地过滤** | ✅ 硬需求；无服务端 min_likes |
| 评论 ≥ | 返回 `comment_count` → **本地过滤** | ✅ 硬需求 |
| 播放 ≥ | 返回 `video_play_count` / `video_view_count` → 本地过滤 | 可选，对齐 YouTube 播放 |
| 精确日期 | 返回 `taken_at` → 本地再滤 | 若 UI 要「指定某一天」可用 |
| 转发 ≥ | 无对应字段 | IG 不做 |
| 收藏 ≥ | 响应基本无 save_count | 明确不做 |

### 5.2 API 调用

```http
GET https://api.scrapecreators.com/v2/instagram/reels/search
  ?query={关键词}
  &date_posted=last-week
  &page=1
Header: x-api-key: {SCRAPECREATORS_API_KEY}
```

文档：https://docs.scrapecreators.com/v2/instagram/reels/search

### 5.3 响应关键字段（文档样例）

| 字段 | 用途 |
|------|------|
| `shortcode` / `url` | 下载地址 |
| `caption` | 标题/描述 |
| `like_count` | 点赞筛选（硬需求） |
| `comment_count` | 评论筛选（硬需求）/ 展示 |
| `video_play_count` / `video_view_count` | 播放筛选/展示（可选） |
| `taken_at` | 日期展示与本地精确过滤 |
| `video_duration` | 时长 |

说明：该端点通过 Google 索引绕过 IG 登录墙，结果**不如 YouTube 实时/完整**，产品文案需接受这一点。

### 5.4 日期参数与前端映射建议

| 前端选项（建议） | `date_posted` | 备注 |
|------------------|---------------|------|
| 全部 | 不传或忽略 | |
| 近 24 小时 | `last-day` | |
| 近一周 | `last-week` | |
| 近一月 | `last-month` | |
| 近一年 | `last-year` | |
| 指定日期 | 不传 `date_posted`，用 `taken_at` 本地滤 | 或先拉 `last-month`/`last-year` 再滤 |

### 5.5 建议统一输出字段

| 字段 | 来源 |
|------|------|
| `id` | media id 或 shortcode |
| `title` | `caption` 截断 |
| `url` | `url` 或 `https://www.instagram.com/reel/{shortcode}/` |
| `uploader` | owner username（若有） |
| `like_count` | `like_count` |
| `comment_count` | `comment_count` |
| `view_count` | `video_play_count` 优先，否则 `video_view_count` |
| `upload_date` | `taken_at` → `YYYYMMDD` |
| `thumbnail` | 封面图（若有） |
| `duration` | `video_duration` |
| `platform` | `"instagram"` |

### 5.6 环境变量（建议）

```env
SCRAPECREATORS_API_KEY=...
INSTAGRAM_SEARCH_MAX_RESULTS=20
INSTAGRAM_SEARCH_POOL=40
```

### 5.7 建议后端文件

```
backend/instagram_search.py
backend/api_instagram_search.py   # POST /api/instagram/search 、可选 search-download
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
- **IG**：`min_likes` / `min_comments` / `min_views` → 本地过滤；`date_filter` → 映射 `date_posted` 或 `taken_at`；忽略 `min_retweets`

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
2. **再做 IG + ScrapeCreators**（本地过滤点赞/评论/播放）
3. 两边都通后再接 `search-download` SSE
4. 开源自建（twscrape / instagrapi）仅作灾备，不进默认路径

### POC 验收清单

- [x] X：关键词 + `min_faves` + `min_retweets` + `min_replies` + 日期区间 → 返回可点开的 `x.com/status/...`
- [x] X：结果含 `like_count` / `retweet_count` / `comment_count`，且低于阈值的条目被滤掉
- [ ] X：结果能被现有 yt-dlp 下载（必要时 Cookie）
- [x] IG：关键词 + `date_posted` → 返回 reel URL
- [x] IG：本地 `like_count` / `comment_count`（及可选 `video_play_count`）过滤正确
- [ ] IG：yt-dlp 或直链可下载（注意登录墙，可能需 Cookie）
- [x] 失败时错误信息可读（额度不足 / Actor 失败 / 无结果）

---

## 9. 风险与合规

1. 均为**非官方**数据源，需法务评估 ToS / 商用合规
2. Apify / ScrapeCreators 上游变更会导致字段或成功率波动，需监控失败率
3. IG 关键词依赖索引，**时效与召回弱于 YouTube**
4. X/IG 下载仍可能触发风控，下载 Cookie 与搜索 API Key 分开管理
5. 密钥只放环境变量 / secrets，勿写入仓库

---

## 10. 关键链接

| 资源 | URL |
|------|-----|
| Apify X Advanced Search（推荐） | https://apify.com/api-ninja/x-twitter-advanced-search |
| Apify X Advanced Search（备选） | https://apify.com/mikolabs/x-twitter-advanced-search-tweet-scraper |
| ScrapeCreators IG Reels Search | https://docs.scrapecreators.com/v2/instagram/reels/search |
| ScrapeCreators IG 总览 | https://scrapecreators.com/instagram-api |
| X 高级搜索运算符参考 | https://github.com/igorbrigadir/twitter-advanced-search |
| 现有 YouTube 搜索 | `backend/youtube_search.py` |

---

## 11. 一句话结论

- **X**：用 **Apify Advanced Search**，服务端做关键词 + 点赞（`min_faves`）+ 转发（`min_retweets`）+ 评论/回复（`min_replies`）+ 日期（+ 仅视频）  
- **Instagram**：用 **ScrapeCreators Reels Search**，请求做关键词 + 日期档，本地做点赞 + 评论（+ 可选播放）；无转发  
- **收藏**：两边都不做  
- **下载**：统一走现有 yt-dlp / bulk 流水线
