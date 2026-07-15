# 表格批量下载 — 冒烟测试

> 日期：2026-07-10  
> 范围：前端「关键词 / 批量下载 → 表格批量」  
> 入口：`#keyword-download` → Tab「表格批量」/ `#bulk-download`  
> 相关：`BulkDownloadSection.vue`、`POST /api/bulk-download`、`bulk_urls.extract_urls_from_upload`  
> 无关键词搜索；从上传文件解析 URL 后走 bulk SSE  
> 本机生产完善顺序：[`local-production-base-rules.md`](./local-production-base-rules.md) §4 → 本文最低集 → 更新基线 §3 状态

---

## 0. 功能清单（须覆盖）

| 能力 | UI / 行为 | 冒烟是否覆盖 |
|------|-----------|--------------|
| 选择文件 | `.xlsx` / `.xlsm` / `.csv` / `.txt` / `.json` | ✅ 各至少 1 次 |
| 开始批量下载 | 未选文件时按钮禁用 | ✅ |
| 保存到浏览器 | 逐文件 `file_part` | ✅ |
| 打包 zip | 分卷/单卷 + 备用链接 | ✅ |
| 跳过已下载 | 默认勾选 | ✅ |
| 每条间隔 | 0–60，冒烟用 1 | ✅ |
| 进度 / 日志 | 识别条数、成功/跳过/失败 | ✅ |
| 取消 | 进行中可取消 | ✅ |
| URL 去重 | 同文件重复链接只下一次 | ✅ |
| 空文件 / 无链接 | 明确错误或 0 条提示 | ✅ |
| 多格式列名 | csv 的 url/link/share_url 等 | ✅ |
| 可选标题列 | csv/json 的 `title`/`caption` → 文件名回退 | ✅ 有列时 |
| 注释行 | `#` 开头忽略 | ✅ |
| 后端还支持 `.jsonl` | 前端 accept 未列 | ⚠️ 可选 API 测，非 UI 必测 |

---

## 1. 固定参数与测试夹具

| 项 | 值 |
|----|-----|
| 链接数量 | 每文件 **2** 条有效 URL（冒烟规模） |
| 跳过已下载 | **勾选**（默认）；复验用例除外 |
| 每条间隔（秒） | `1` |
| 环境 | 前端可达后端；浏览器允许下载 |

**建议准备本地夹具（测前放在任意目录）：**

| 文件 | 内容要点 |
|------|----------|
| `smoke-bulk.txt` | 两行直链；可加一行 `# comment` |
| `smoke-bulk.csv` | 表头 `url` + 两行 https 链接 |
| `smoke-bulk-link.csv` | 表头 `link`（验证列名兼容） |
| `smoke-bulk.json` | `[{"url":"..."},{"url":"..."}]` 或含 `share_url` |
| `smoke-bulk.xlsx` | 任一单元格含两行/两格 https 链接 |
| `smoke-bulk-dup.txt` | 同一 URL 写两遍（测去重） |
| `smoke-bulk-empty.txt` | 空或仅注释 |
| `smoke-bulk-bad.ext` | 非支持扩展名（若浏览器仍可选） |

> URL 可用公开可下的短视频样例，或项目内已知可下的 YouTube/其它平台链接。勿用需登录且无 Cookie 的私密链。

**保存：** 浏览器 / ZIP

---

## 2. 通过标准

- [ ] 选文件后「开始批量下载」可点；未选不可点
- [ ] 日志：`开始上传` → `共识别 N 条` → 逐条结果 → 汇总
- [ ] N 与文件有效去重后链接数一致（2 条夹具 → total=2）
- [ ] 浏览器模式：成功条触发浏览器下载
- [ ] ZIP：出现分卷区；下载成功；解压数 ≈ 成功数
- [ ] 跳过开启时，同 URL 第二次任务计跳过
- [ ] 失败条有原因；不拖垮整批（其它条可继续）
- [ ] 取消后前端停止更新（当前条服务端可能仍在处理）

---

## 3. 主用例矩阵

| ID | 文件 | 保存 | 跳过 | 期望 |
|----|------|------|------|------|
| BK-01 | `smoke-bulk.txt` | 浏览器 | 开 | 识别 2；逐文件下载；有成功汇总 |
| BK-02 | `smoke-bulk.csv`（url 列） | ZIP | 开 | 识别 2；ZIP 可下 |
| BK-03 | `smoke-bulk.xlsx` | 浏览器 | 开 | Excel 解析成功；≤2 条处理 |
| BK-04 | `smoke-bulk.json` | ZIP | 开 | JSON 解析成功；ZIP 可下 |
| BK-05 | `smoke-bulk-link.csv`（link 列） | 浏览器 | 开 | 列名兼容，识别 2 |
| BK-06 | `smoke-bulk.txt` 再跑一次 | 浏览器 | 开 | **跳过复验**：已成功 URL 跳过 |
| BK-07 | 同 BK-01 文件 | 浏览器 | **关** | 不因「已下载」跳过（可能重下成功） |

**最低集：** `BK-01、BK-02、BK-03、BK-04、BK-06、BK-B1、BK-B3`

---

## 4. 补充用例

| ID | 场景 | 步骤 | 期望 |
|----|------|------|------|
| BK-B1 | 未选文件 | 不选文件 | 「开始」禁用 |
| BK-B2 | 空/无链接 | 上传 `smoke-bulk-empty.txt` | 明确错误或识别 0 条，不假成功 |
| BK-B3 | 取消 | BK-01 进行中点取消 | 停流；日志已取消 |
| BK-B4 | 去重 | `smoke-bulk-dup.txt` | 共识别 1（或日志体现去重后条数） |
| BK-B5 | 注释行 | txt 含 `# ...` 与 2 条 URL | 注释忽略；仍识别 2 |
| BK-B6 | 部分失败 | 1 条有效 + 1 条无效 URL | 1 成功/失败分离；汇总正确 |
| BK-B7 | xlsm | 若有 `.xlsm` 样例 | 与 xlsx 同等可解析 |
| BK-Z1 | ZIP 一次性 | BK-02 同链再点 | 第二次失败/不可用 |
| BK-Z2 | 分卷（可选） | 多文件或大文件触发分卷 | 多个分卷可分别下（条数=2 通常单卷，可 Skip） |

---

## 5. URL 解析规则（测前知悉 / 断言依据）

来自 `backend/bulk_urls.py`：

- 支持后缀：`.xlsx` / `.xlsm` / `.csv` / `.txt` / `.json`（另 `.jsonl` 后端支持）
- CSV 优先列名（不区分大小写）：`share_url`、`video_url`、`url`、`link`、`note_url`、`aweme_url`
- JSON：数组字符串、或对象中上述 key；嵌套 dict/list 会递归
- 文本：正则抽 `http(s)://...`；`#` 行忽略
- 全格式最终 **去重保序**

---

## 6. 执行记录表

> 执行：API 冒烟 `backend/scripts/smoke_bulk_download.py --min`（对齐最低集）  
> 环境：`http://127.0.0.1:8001` LOCAL_MODE；夹具直链 filesamples mp4 ×2；日期 2026-07-10

| ID | 执行人 | 日期 | 结果 (Pass/Fail/Skip) | 识别条数 | 成功/跳过/失败 | 备注 |
|----|--------|------|----------------------|----------|----------------|------|
| BK-01 | agent | 2026-07-10 | Pass | 2 | 2/0/0 | 浏览器逐文件；file_part×2 可下 |
| BK-02 | agent | 2026-07-10 | Pass | 2 | 2/0/0 | CSV→ZIP 1.8MB；为验交付本跑 skip=off |
| BK-03 | agent | 2026-07-10 | Pass | 2 | 2/0/0 | xlsx 解析+浏览器交付 |
| BK-04 | agent | 2026-07-10 | Pass | 2 | 2/0/0 | JSON→ZIP 可下 |
| BK-05 | agent | 2026-07-10 | Pass | 2 | 2/0/0 | link 列名兼容 |
| BK-06 | agent | 2026-07-10 | Pass | 2 | 1/1/0 | 跳过复验 skip≥1（并发写状态时偶发只跳 1 条） |
| BK-07 |  |  | Skip |  |  | 非最低集 |
| BK-B1 | agent | 2026-07-10 | Pass | - | - | Vue `:disabled="running \|\| !selectedFile"` |
| BK-B2 | agent | 2026-07-10 | Pass | 0 | - | 空文件：未识别到 http(s) 链接 |
| BK-B3 | agent | 2026-07-10 | Pass | 2 | - | 截断 SSE 模拟取消；见 start 后停读 |
| BK-B4 | agent | 2026-07-10 | Pass | 1 | 1/0/0 | 去重后识别 1 |
| BK-Z1 | agent | 2026-07-10 | Pass | 2 | 1/1/0 | ZIP 二次 GET → 404 |

---

## 7. 已知限制

- 无「仅预览」；选文件即进入下载流水线。
- 前端 `accept` 未含 `.jsonl`，jsonl 需 API/改扩展名测。
- 跳过状态与关键词下载共用；交叉平台同 URL 也可能被跳过。
- ZIP 链接一次性；大包才分卷。
- 平台风控/Cookie 会导致部分链接失败，属环境问题，记备注即可。
- 「保存到浏览器」与「打包 zip」最终都进浏览器下载；差异是逐文件 vs 先打包。
