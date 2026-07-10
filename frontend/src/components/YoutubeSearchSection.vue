<template>
  <component
    :is="embedded ? 'div' : 'section'"
    :id="embedded ? undefined : 'youtube-search'"
    :class="embedded ? '' : 'py-12 sm:py-16 bg-white border-t border-border-light'"
    :aria-labelledby="embedded ? undefined : 'youtube-search-heading'"
  >
    <div :class="embedded ? '' : 'max-w-3xl mx-auto px-4 sm:px-6'">
      <div v-if="!embedded" class="text-center mb-8">
        <h2 id="youtube-search-heading" class="text-2xl sm:text-3xl font-bold text-text-primary">
          YouTube 关键词下载
        </h2>
      </div>

      <div class="bg-bg-section rounded-2xl border border-border-light shadow-sm p-5 sm:p-6 space-y-5">
        <div>
          <label class="block text-sm font-medium text-text-primary mb-2">关键词</label>
          <input
            v-model="query"
            type="text"
            maxlength="200"
            placeholder="例如：AI 教程、旅行 vlog"
            :disabled="running"
            class="w-full px-3 py-2.5 rounded-xl border border-border text-text-primary text-sm focus:ring-2 focus:ring-primary/30 focus:border-primary disabled:opacity-50"
            @keydown.enter.prevent="primaryAction()"
          />
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <div>
            <label class="block text-xs font-medium text-text-muted mb-1">最多下载条数</label>
            <input
              v-model.number="maxResults"
              type="number"
              min="1"
              max="50"
              step="1"
              :disabled="running || hasPreviewReady"
              class="w-full px-3 py-2 rounded-lg border border-border text-text-primary text-sm focus:ring-2 focus:ring-primary/30 focus:border-primary disabled:opacity-50"
            />
          </div>
          <div>
            <label class="block text-xs font-medium text-text-muted mb-1">播放量 ≥</label>
            <input
              v-model.number="minViews"
              type="number"
              min="0"
              step="1000"
              placeholder="0 表示不限"
              :disabled="running || hasPreviewReady"
              class="w-full px-3 py-2 rounded-lg border border-border text-text-primary text-sm focus:ring-2 focus:ring-primary/30 focus:border-primary disabled:opacity-50"
            />
          </div>
          <div>
            <label class="block text-xs font-medium text-text-muted mb-1">点赞 ≥</label>
            <input
              v-model.number="minLikes"
              type="number"
              min="0"
              step="100"
              placeholder="0 表示不限"
              :disabled="running || hasPreviewReady"
              class="w-full px-3 py-2 rounded-lg border border-border text-text-primary text-sm focus:ring-2 focus:ring-primary/30 focus:border-primary disabled:opacity-50"
            />
          </div>
          <div>
            <label class="block text-xs font-medium text-text-muted mb-1">发布日期</label>
            <select
              v-model="dateFilter"
              :disabled="running || hasPreviewReady"
              class="w-full px-3 py-2 rounded-lg border border-border text-text-primary text-sm focus:ring-2 focus:ring-primary/30 focus:border-primary disabled:opacity-50 bg-white"
            >
              <option value="all">不限</option>
              <option value="today">今日</option>
              <option value="week">近一周</option>
              <option value="month">近一月</option>
              <option value="date">指定日期</option>
            </select>
          </div>
        </div>
        <div v-if="dateFilter === 'date'" class="sm:max-w-xs">
          <label class="block text-xs font-medium text-text-muted mb-1">选择日期</label>
          <input
            v-model="uploadDate"
            type="date"
            :disabled="running || hasPreviewReady"
            class="w-full px-3 py-2 rounded-lg border border-border text-text-primary text-sm focus:ring-2 focus:ring-primary/30 focus:border-primary disabled:opacity-50"
          />
        </div>

        <div class="flex flex-col sm:flex-row sm:flex-wrap gap-4 text-sm">
          <label class="inline-flex items-center gap-2 cursor-pointer select-none">
            <input
              v-model="saveMode"
              type="radio"
              value="browser"
              :disabled="running"
              class="border-border text-primary focus:ring-primary/30"
            />
            <span class="text-text-secondary">保存到浏览器下载目录</span>
          </label>
          <label class="inline-flex items-center gap-2 cursor-pointer select-none">
            <input
              v-model="packZip"
              type="checkbox"
              :disabled="running"
              class="rounded border-border text-primary focus:ring-primary/30"
            />
            <span class="text-text-secondary">打包 zip</span>
          </label>
          <label class="inline-flex items-center gap-2 cursor-pointer select-none" title="按服务端历史记录跳过；浏览器/ZIP 临时目录不验本地文件">
            <input v-model="skipCompleted" type="checkbox" :disabled="running" class="rounded border-border text-primary focus:ring-primary/30" />
            <span class="text-text-secondary">跳过已下载过的链接</span>
            <span class="text-xs text-text-muted">（按历史；浏览器下载不验本地文件）</span>
          </label>
        </div>

        <div class="flex flex-wrap items-end gap-4">
          <div>
            <label class="block text-xs font-medium text-text-muted mb-1">每条间隔（秒）</label>
            <input
              v-model.number="delaySeconds"
              type="number"
              min="0"
              max="60"
              step="1"
              :disabled="running"
              class="w-24 px-3 py-2 rounded-lg border border-border text-text-primary text-sm focus:ring-2 focus:ring-primary/30 focus:border-primary disabled:opacity-50"
            />
          </div>
          <button
            type="button"
            :disabled="running || (hasPreviewReady ? !previewUrls.length : !canStart)"
            class="inline-flex items-center gap-2 px-6 py-2.5 rounded-xl bg-primary hover:bg-primary-dark text-white font-medium text-sm shadow-md disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer transition-colors"
            @click="primaryAction"
          >
            <svg v-if="running" class="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            {{ primaryButtonLabel }}
          </button>
          <button
            v-if="!hasPreviewReady"
            type="button"
            :disabled="running || !canStart"
            class="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-border bg-white text-text-primary font-medium text-sm hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer transition-colors"
            @click="previewOnly"
          >
            仅预览结果
          </button>
          <button
            v-else
            type="button"
            :disabled="running"
            class="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl border border-border bg-white text-text-primary font-medium text-sm hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer transition-colors"
            @click="clearPreview"
          >
            重新搜索
          </button>
          <button
            v-if="running"
            type="button"
            class="text-sm text-text-secondary hover:text-primary cursor-pointer"
            @click="abortRun"
          >
            取消
          </button>
        </div>

        <div
          v-if="previewResults.length || belowThresholdResults.length"
          class="rounded-xl border border-border bg-white overflow-hidden"
        >
          <ul v-if="previewResults.length" class="max-h-56 overflow-y-auto divide-y divide-border/60 text-sm">
            <li v-for="item in previewResults" :key="item.id || item.url" class="px-3 py-2.5">
              <a
                :href="item.url"
                target="_blank"
                rel="noopener noreferrer"
                class="font-medium text-text-primary hover:text-primary line-clamp-1"
              >{{ item.title }}</a>
              <p class="mt-0.5 text-xs text-text-muted">
                播放 {{ formatCount(item.view_count) }}
                · 点赞 {{ formatCount(item.like_count) }}
                <span v-if="item.upload_date_display"> · {{ item.upload_date_display }}</span>
              </p>
            </li>
          </ul>
          <div v-if="belowThresholdResults.length" class="border-t border-border">
            <ul class="max-h-40 overflow-y-auto divide-y divide-border/60 text-sm">
              <li
                v-for="item in belowThresholdResults"
                :key="`below-${item.id || item.url}`"
                class="px-3 py-2.5"
              >
                <a
                  :href="item.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="font-medium text-text-secondary hover:text-primary line-clamp-1"
                >{{ item.title }}</a>
                <p class="mt-0.5 text-xs text-amber-700">
                  {{ dateFilter === 'today' ? '今日播放' : '播放' }} {{ formatCount(item.view_count) }}
                  · 点赞 {{ formatCount(item.like_count) }}
                </p>
              </li>
            </ul>
          </div>
        </div>

        <div v-if="total > 0" class="text-sm text-text-secondary">
          进度：<span class="font-medium text-text-primary">{{ currentIndex }}</span> / {{ total }}
          <span v-if="summaryText" class="ml-2 text-text-muted">{{ summaryText }}</span>
        </div>

        <div
          ref="logBox"
          class="rounded-xl border border-border bg-gray-50/80 max-h-64 overflow-y-auto p-3 font-mono text-xs text-text-secondary space-y-1"
          role="log"
          aria-live="polite"
        >
          <p v-if="!logLines.length" class="text-text-muted">日志将显示在这里…</p>
          <p v-for="(line, i) in logLines" :key="i" :class="lineClass(line)">{{ line }}</p>
        </div>

        <div
          v-if="pendingZipParts.length"
          id="youtube-zip-actions"
          class="rounded-xl border-2 border-primary/35 bg-primary-light/40 p-4 text-sm text-text-secondary space-y-3 scroll-mt-24"
        >
          <p class="text-xs leading-relaxed text-text-primary font-medium">
            <template v-if="pendingZipParts.length === 1">
              ZIP 已生成。请任选一种方式保存；同一链接<strong>仅可使用一次</strong>。
            </template>
            <template v-else>
              已拆成 {{ pendingZipParts.length }} 个分卷。可边下边传已就绪分卷。
            </template>
          </p>
          <ul class="space-y-2 max-h-64 overflow-y-auto">
            <li
              v-for="part in pendingZipParts"
              :key="part.part"
              class="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 bg-white/80 px-3 py-2"
            >
              <span class="text-xs font-medium text-text-primary min-w-[4.5rem]">
                分卷 {{ part.part }}
              </span>
              <span class="text-xs text-text-muted">
                {{ part.file_count }} 个文件 · {{ formatBytes(part.zip_bytes) }}
              </span>
              <button
                type="button"
                class="ml-auto inline-flex items-center px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-medium hover:bg-primary-dark cursor-pointer"
                @click="onDownloadZipPart(part)"
              >
                下载
              </button>
              <a
                :href="part.url"
                class="inline-flex items-center px-3 py-1.5 rounded-lg border border-border bg-white text-text-primary text-xs font-medium hover:bg-gray-50"
                :download="`youtube-batch-part${String(part.part).padStart(2, '0')}.zip`"
              >
                备用链接
              </a>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </component>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { searchYoutube, youtubeSearchDownloadStream } from '../api/youtube.js'
import { bulkDownloadUrlsStream } from '../api/bulk.js'

defineProps({
  embedded: { type: Boolean, default: false },
})

const query = ref('')
const maxResults = ref(10)
const minViews = ref(0)
const minLikes = ref(0)
const delaySeconds = ref(2)
const dateFilter = ref('all')
const uploadDate = ref('')
/** 默认浏览器下载目录；勾选「打包 zip」则打 ZIP 后再下到浏览器 */
const saveMode = ref('browser')
const packZip = ref(false)
const skipCompleted = ref(true)
/** 隐藏高级项：跳过时默认校验服务器上是否仍有文件 */
const verifyFile = ref(true)

watch(saveMode, (v) => {
  if (v === 'browser') packZip.value = false
})
watch(packZip, (v) => {
  if (v) saveMode.value = 'browser'
})
const packForBrowser = computed(() => packZip.value)
const deliverFiles = computed(() => !packZip.value)

const running = ref(false)
const phase = ref('') // search | download
const total = ref(0)
const currentIndex = ref(0)
const okCount = ref(0)
const skipCount = ref(0)
const failCount = ref(0)
const logLines = ref([])
const logBox = ref(null)
const abortController = ref(null)
const pendingZipParts = ref([])
const previewResults = ref([])
const belowThresholdResults = ref([])
/** 仅预览成功后为 true：主按钮变为「下载」，不再重新搜索 */
const hasPreviewReady = ref(false)

const canStart = computed(() => Boolean(query.value.trim()))

const previewUrls = computed(() =>
  (previewResults.value || [])
    .map((item) => item?.url)
    .filter((u) => typeof u === 'string' && /^https?:\/\//i.test(u))
)

const primaryButtonLabel = computed(() => {
  if (running.value) {
    return phase.value === 'search' ? '正在搜索…' : '批量下载进行中…'
  }
  if (hasPreviewReady.value) {
    return `下载（${previewUrls.value.length}）`
  }
  return '搜索并下载'
})

const summaryText = computed(() => {
  if (!total.value) return ''
  return `成功 ${okCount.value} · 跳过 ${skipCount.value} · 失败 ${failCount.value}`
})

watch(skipCompleted, (v) => {
  if (!v) verifyFile.value = false
})

watch([query, maxResults, minViews, minLikes, dateFilter, uploadDate], () => {
  if (hasPreviewReady.value && !running.value) {
    hasPreviewReady.value = false
  }
})

function lineClass(line) {
  if (line.includes('失败') || line.includes('错误')) return 'text-red-600'
  if (line.includes('跳过')) return 'text-amber-700'
  if (line.includes('成功')) return 'text-green-700'
  return ''
}

function formatBytes(n) {
  if (!n || n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`
}

function formatCount(n) {
  if (n == null || n === '') return '未知'
  const v = Number(n)
  if (!Number.isFinite(v)) return '未知'
  if (v >= 1e8) return `${(v / 1e8).toFixed(1)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(1)}万`
  return String(Math.trunc(v))
}

function pushLog(msg) {
  logLines.value = [...logLines.value, `[${new Date().toLocaleTimeString()}] ${msg}`]
  requestAnimationFrame(() => {
    const el = logBox.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function startBrowserDownload(url, filename = 'video.mp4') {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

function startBrowserZipDownload(url, filename = 'youtube-batch.zip') {
  startBrowserDownload(url, filename)
}

function addZipPart(raw) {
  if (!raw?.url) return
  const url = new URL(raw.url, window.location.origin).href
  const part = {
    part: raw.part ?? pendingZipParts.value.length + 1,
    url,
    zip_bytes: raw.zip_bytes ?? 0,
    source_bytes: raw.source_bytes ?? 0,
    file_count: raw.file_count ?? 0,
  }
  const idx = pendingZipParts.value.findIndex((p) => p.part === part.part)
  if (idx >= 0) {
    pendingZipParts.value[idx] = part
    pendingZipParts.value = [...pendingZipParts.value]
  } else {
    pendingZipParts.value = [...pendingZipParts.value, part].sort((a, b) => a.part - b.part)
  }
}

function scrollToZipActions() {
  nextTick(() => {
    document.getElementById('youtube-zip-actions')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  })
}

function onDownloadZipPart(part) {
  if (!part?.url) return
  try {
    const name = `youtube-batch-part${String(part.part).padStart(2, '0')}.zip`
    startBrowserZipDownload(part.url, name)
    pushLog(`已发起分卷 ${part.part} 下载（${formatBytes(part.zip_bytes)}）`)
  } catch (e) {
    pushLog(`分卷 ${part.part} 下载失败：${e.message || e}`)
  }
}

function resetProgressState({ clearPreview = true, clearLogs = true } = {}) {
  total.value = 0
  currentIndex.value = 0
  okCount.value = 0
  skipCount.value = 0
  failCount.value = 0
  pendingZipParts.value = []
  if (clearLogs) logLines.value = []
  if (clearPreview) {
    previewResults.value = []
    belowThresholdResults.value = []
    hasPreviewReady.value = false
  }
}

function handleDownloadEvent(data) {
  if (abortController.value?.signal.aborted) return
  const ev = data.event
  if (ev === 'searching') {
    phase.value = 'search'
    pushLog('正在搜索…')
  } else if (ev === 'search_done') {
    phase.value = 'download'
    previewResults.value = data.results || []
    belowThresholdResults.value = data.below_threshold_results || []
    pushLog(`找到 ${data.total || 0} 条`)
  } else if (ev === 'start') {
    phase.value = 'download'
    total.value = data.total || 0
    pushLog(`开始批量下载，共 ${data.total} 条`)
  } else if (ev === 'file_part') {
    if (data.url) {
      const href = new URL(data.url, window.location.origin).href
      startBrowserDownload(href, data.filename || 'video.mp4')
    }
  } else if (ev === 'zip_part') {
    addZipPart(data)
    pushLog(`分卷 ${data.part} 已就绪：${data.file_count} 个文件，约 ${formatBytes(data.zip_bytes)}`)
    scrollToZipActions()
  } else if (ev === 'item') {
    currentIndex.value = data.index || 0
    const short = (data.url || '').length > 72 ? `${(data.url || '').slice(0, 72)}…` : (data.url || '')
    if (data.status === 'skip') {
      skipCount.value += 1
      pushLog(`[${data.index}/${data.total}] 跳过 ${short} — ${data.message || ''}`)
    } else if (data.status === 'ok') {
      okCount.value += 1
      pushLog(`[${data.index}/${data.total}] 成功 ${short} → ${data.filename || ''}`)
    } else if (data.status === 'fail') {
      failCount.value += 1
      pushLog(`[${data.index}/${data.total}] 失败 ${short} — ${data.message || '未知错误'}`)
    }
  } else if (ev === 'done') {
    okCount.value = data.ok ?? okCount.value
    skipCount.value = data.skip ?? skipCount.value
    failCount.value = data.fail ?? failCount.value
    pushLog(`全部结束：成功 ${data.ok}，跳过 ${data.skip}，失败 ${data.fail}`)
    if (Array.isArray(data.zip_parts) && data.zip_parts.length) {
      for (const part of data.zip_parts) addZipPart(part)
      pushLog(
        data.zip_part_count > 1
          ? `共 ${data.zip_part_count} 个分卷可下载。`
          : 'ZIP 已就绪：请在下方点击下载。'
      )
      scrollToZipActions()
    } else if (data.zip_url && (data.ok ?? 0) > 0) {
      addZipPart({
        part: 1,
        url: data.zip_url,
        zip_bytes: data.zip_bytes,
        source_bytes: data.source_bytes,
        file_count: data.zip_file_count,
      })
      pushLog('ZIP 已就绪：请在下方点击下载。')
      scrollToZipActions()
    }
  } else if (ev === 'error') {
    pushLog(`错误：${data.message || '未知错误'}`)
  }
}

function clearPreview() {
  if (running.value) return
  hasPreviewReady.value = false
  previewResults.value = []
  belowThresholdResults.value = []
}

function primaryAction() {
  if (hasPreviewReady.value) {
    downloadPreview()
  } else {
    startSearchAndDownload()
  }
}

async function previewOnly() {
  if (!canStart.value || running.value) return
  running.value = true
  phase.value = 'search'
  resetProgressState({ clearPreview: true, clearLogs: true })
  pushLog(`预览搜索：${query.value.trim()}`)
  try {
    const data = await searchYoutube({
      query: query.value.trim(),
      maxResults: maxResults.value || 10,
      minViews: minViews.value || 0,
      minLikes: minLikes.value || 0,
      dateFilter: dateFilter.value,
      uploadDate: uploadDate.value,
    })
    previewResults.value = data.results || []
    belowThresholdResults.value = data.below_threshold_results || []
    pushLog(`找到 ${data.total || 0} 条`)
    if (data.total > 0 && previewUrls.value.length) {
      hasPreviewReady.value = true
    } else {
      hasPreviewReady.value = false
      if (!(belowThresholdResults.value.length > 0)) {
        pushLog('没有符合条件的视频')
      }
    }
  } catch (e) {
    hasPreviewReady.value = false
    pushLog(`搜索失败：${e.message || e}`)
  } finally {
    running.value = false
    phase.value = ''
  }
}

async function downloadPreview() {
  const urls = previewUrls.value
  if (!urls.length || running.value) return

  running.value = true
  phase.value = 'download'
  resetProgressState({ clearPreview: false, clearLogs: false })
  abortController.value = new AbortController()
  pushLog(`开始下载，共 ${urls.length} 条`)

  try {
    await bulkDownloadUrlsStream(urls, {
      skipCompleted: skipCompleted.value,
      verifyFile: verifyFile.value,
      delaySeconds: delaySeconds.value,
      downloadDir: '',
      packForBrowser: packForBrowser.value,
      deliverFiles: deliverFiles.value,
      sourceName: `youtube-preview:${query.value.trim() || 'results'}`,
      signal: abortController.value.signal,
      onEvent: handleDownloadEvent,
    })
  } catch (e) {
    if (e.name === 'AbortError') {
      pushLog('已取消（连接已断开，服务端可能仍在处理当前这一条）')
    } else {
      pushLog(`请求异常：${e.message || e}`)
    }
  } finally {
    running.value = false
    phase.value = ''
    abortController.value = null
  }
}

async function startSearchAndDownload() {
  if (!canStart.value || running.value) return

  running.value = true
  phase.value = 'search'
  resetProgressState({ clearPreview: true, clearLogs: true })
  abortController.value = new AbortController()

  const q = query.value.trim()
  pushLog(`开始：${q}`)

  try {
    await youtubeSearchDownloadStream({
      query: q,
      maxResults: maxResults.value || 10,
      minViews: minViews.value || 0,
      minLikes: minLikes.value || 0,
      dateFilter: dateFilter.value,
      uploadDate: uploadDate.value,
      skipCompleted: skipCompleted.value,
      verifyFile: verifyFile.value,
      delaySeconds: delaySeconds.value,
      downloadDir: '',
      packForBrowser: packForBrowser.value,
      deliverFiles: deliverFiles.value,
      signal: abortController.value.signal,
      onEvent: handleDownloadEvent,
    })
  } catch (e) {
    if (e.name === 'AbortError') {
      pushLog('已取消（连接已断开，服务端可能仍在处理当前这一条）')
    } else {
      pushLog(`请求异常：${e.message || e}`)
    }
  } finally {
    running.value = false
    phase.value = ''
    abortController.value = null
  }
}

function abortRun() {
  abortController.value?.abort()
  pushLog('正在取消…')
}
</script>
