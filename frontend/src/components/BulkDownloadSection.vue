<template>
  <component
    :is="embedded ? 'div' : 'section'"
    :id="embedded ? undefined : 'bulk-download'"
    :class="embedded ? '' : 'py-12 sm:py-16 bg-white border-t border-border-light'"
    :aria-labelledby="embedded ? undefined : 'bulk-heading'"
  >
    <div :class="embedded ? '' : 'max-w-3xl mx-auto px-4 sm:px-6'">
      <div v-if="!embedded" class="text-center mb-8">
        <h2 id="bulk-heading" class="text-2xl sm:text-3xl font-bold text-text-primary">
          表格批量下载
        </h2>
      </div>

      <div class="bg-bg-section rounded-2xl border border-border-light shadow-sm p-5 sm:p-6 space-y-5">
        <div>
          <label class="block text-sm font-medium text-text-primary mb-2">选择文件</label>
          <input
            ref="fileInput"
            type="file"
            accept=".xlsx,.xlsm,.csv,.txt,.json"
            :disabled="running"
            class="block w-full text-sm text-text-secondary file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-medium file:bg-primary-light file:text-primary hover:file:bg-blue-100 cursor-pointer disabled:opacity-50"
          />
          <p class="mt-1.5 text-xs text-text-muted">
            支持 .xlsx / .xlsm / .csv / .txt / .json
          </p>
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
          <label class="inline-flex items-center gap-2 cursor-pointer select-none">
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
            :disabled="running || !selectedFile"
            class="inline-flex items-center gap-2 px-6 py-2.5 rounded-xl bg-primary hover:bg-primary-dark text-white font-medium text-sm shadow-md disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer transition-colors"
            @click="start"
          >
            <svg v-if="running" class="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            {{ running ? '批量下载进行中…' : '开始批量下载' }}
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
          id="bulk-zip-actions"
          class="rounded-xl border-2 border-primary/35 bg-primary-light/40 p-4 text-sm text-text-secondary space-y-3 scroll-mt-24"
        >
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
                :download="`batch-download-part${String(part.part).padStart(2, '0')}.zip`"
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
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { bulkDownloadStream } from '../api/bulk.js'
import { friendlyError } from '../api/friendlyError.js'

defineProps({
  embedded: { type: Boolean, default: false },
})

const fileInput = ref(null)
const logBox = ref(null)
const selectedFile = ref(null)
const running = ref(false)
const skipCompleted = ref(true)
const verifyFile = ref(true)
const delaySeconds = ref(2)
const saveMode = ref('browser')
const packZip = ref(false)

watch(saveMode, (v) => {
  if (v === 'browser') packZip.value = false
})
watch(packZip, (v) => {
  if (v) saveMode.value = 'browser'
})
const packForBrowser = computed(() => packZip.value)
const deliverFiles = computed(() => !packZip.value)

const total = ref(0)
const currentIndex = ref(0)
const okCount = ref(0)
const skipCount = ref(0)
const failCount = ref(0)
const logLines = ref([])
const abortController = ref(null)
/** 分卷 ZIP 列表；任务进行中也会追加已就绪分卷 */
const pendingZipParts = ref([])
const zipChunkMaxBytes = ref(2 * 1024 ** 3)
const zipChunkMaxFiles = ref(25)

const summaryText = computed(() => {
  if (!total.value) return ''
  return `成功 ${okCount.value} · 跳过 ${skipCount.value} · 失败 ${failCount.value}`
})

function lineClass(line) {
  if (line.includes('失败')) return 'text-red-600'
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

/**
 * 同源直链交给浏览器下载（下载栏可立刻显示进度）。
 * 不使用 fetch→blob：blob 会先静默拉完整文件再弹保存，大 ZIP 会像「卡住几分钟」。
 */
function startBrowserDownload(url, filename = 'video.mp4') {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

function startBrowserZipDownload(url, filename = 'batch-download.zip') {
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
    document.getElementById('bulk-zip-actions')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  })
}

function pushLog(msg) {
  logLines.value = [...logLines.value, `[${new Date().toLocaleTimeString()}] ${msg}`]
  requestAnimationFrame(() => {
    const el = logBox.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function onDownloadZipPart(part) {
  if (!part?.url) return
  try {
    const name = `batch-download-part${String(part.part).padStart(2, '0')}.zip`
    startBrowserZipDownload(part.url, name)
  } catch (e) {
    pushLog(`分卷 ${part.part} 下载失败：${friendlyError(e)}`)
  }
}

function syncFile() {
  const f = fileInput.value?.files?.[0]
  selectedFile.value = f || null
}

onMounted(() => {
  fileInput.value?.addEventListener('change', syncFile)
})
onBeforeUnmount(() => {
  fileInput.value?.removeEventListener('change', syncFile)
})

watch(skipCompleted, (v) => {
  if (!v) verifyFile.value = false
})

async function start() {
  const file = fileInput.value?.files?.[0]
  if (!file || running.value) return

  running.value = true
  total.value = 0
  currentIndex.value = 0
  okCount.value = 0
  skipCount.value = 0
  failCount.value = 0
  logLines.value = []
  pendingZipParts.value = []
  abortController.value = new AbortController()

  pushLog(`开始上传：${file.name}`)

  try {
    await bulkDownloadStream(file, {
      signal: abortController.value.signal,
      skipCompleted: skipCompleted.value,
      verifyFile: verifyFile.value,
      delaySeconds: delaySeconds.value,
      downloadDir: '',
      packForBrowser: packForBrowser.value,
      deliverFiles: deliverFiles.value,
      onEvent: (data) => {
        if (abortController.value?.signal.aborted) return
        const ev = data.event
        if (ev === 'start') {
          total.value = data.total || 0
          pushLog(`共识别 ${data.total} 条链接`)
          if (data.zip_chunk_max_bytes) zipChunkMaxBytes.value = data.zip_chunk_max_bytes
          if (data.zip_chunk_max_files) zipChunkMaxFiles.value = data.zip_chunk_max_files
        } else if (ev === 'file_part') {
          if (data.url) {
            const href = new URL(data.url, window.location.origin).href
            startBrowserDownload(href, data.filename || 'video.mp4')
          }
        } else if (ev === 'zip_part') {
          addZipPart(data)
          pushLog(`分卷 ${data.part} 已就绪`)
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
            pushLog(`[${data.index}/${data.total}] 失败 ${short} — ${friendlyError(data.message || '未知错误')}`)
          }
        } else if (ev === 'done') {
          okCount.value = data.ok ?? okCount.value
          skipCount.value = data.skip ?? skipCount.value
          failCount.value = data.fail ?? failCount.value
          pushLog(`全部结束：成功 ${data.ok}，跳过 ${data.skip}，失败 ${data.fail}`)
          if (Array.isArray(data.zip_parts) && data.zip_parts.length) {
            for (const part of data.zip_parts) addZipPart(part)
            scrollToZipActions()
          } else if (data.zip_url && (data.ok ?? 0) > 0) {
            addZipPart({ part: 1, url: data.zip_url, zip_bytes: data.zip_bytes, source_bytes: data.source_bytes, file_count: data.zip_file_count })
            scrollToZipActions()
          }
        } else if (ev === 'error') {
          pushLog(`错误：${friendlyError(data.message || '未知错误')}`)
        }
      },
    })
  } catch (e) {
    if (e.name === 'AbortError') {
      pushLog('已取消（连接已断开，服务端可能仍在处理当前这一条）')
    } else {
      pushLog(`请求异常：${friendlyError(e)}`)
    }
  } finally {
    running.value = false
    abortController.value = null
  }
}

function abortRun() {
  abortController.value?.abort()
  pushLog('正在取消…')
}
</script>
