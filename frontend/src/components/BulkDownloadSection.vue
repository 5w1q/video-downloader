<template>
  <section id="bulk-download" class="py-12 sm:py-16 bg-bg-section border-t border-border-light" aria-labelledby="bulk-heading">
    <div class="max-w-3xl mx-auto px-4 sm:px-6">
      <div class="text-center mb-8">
        <h2 id="bulk-heading" class="text-2xl sm:text-3xl font-bold text-text-primary mb-2">
          表格批量下载
        </h2>
        <p class="text-text-secondary text-sm sm:text-base max-w-xl mx-auto">
          上传 Excel（.xlsx）、CSV 或 TXT，自动识别链接后<strong class="text-text-primary">逐条经浏览器另存为</strong>保存到您的电脑；
          服务端仅在传输时暂存临时文件，传完后即删除，不长期占用服务器磁盘。
          若浏览器提示「拦截了多次下载」，请在地址栏允许本站自动下载多个文件。
        </p>
      </div>

      <div class="bg-white rounded-2xl border border-border-light shadow-sm p-5 sm:p-6 space-y-5">
        <div>
          <label class="block text-sm font-medium text-text-primary mb-2">选择文件</label>
          <input
            ref="fileInput"
            type="file"
            accept=".xlsx,.xlsm,.csv,.txt,.json,.jsonl"
            :disabled="running"
            class="block w-full text-sm text-text-secondary file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-medium file:bg-primary-light file:text-primary hover:file:bg-blue-100 cursor-pointer disabled:opacity-50"
          />
          <p class="mt-1.5 text-xs text-text-muted">支持 .xlsx / .xlsm / .csv / .txt / .json / .jsonl，多列、多工作表中的链接均会扫描。</p>
        </div>

        <div class="flex flex-col sm:flex-row sm:flex-wrap gap-4 text-sm">
          <label class="inline-flex items-center gap-2 cursor-pointer select-none">
            <input v-model="skipCompleted" type="checkbox" :disabled="running" class="rounded border-border text-primary focus:ring-primary/30" />
            <span class="text-text-secondary">跳过本机已标记完成的链接</span>
          </label>
          <button
            type="button"
            class="text-xs text-primary hover:text-primary-dark underline cursor-pointer disabled:opacity-50"
            :disabled="running"
            @click="clearLocalRecords"
          >
            清除本机完成记录
          </button>
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
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { bulkExtractUrls } from '../api/bulk.js'
import { downloadViaServer } from '../api/video.js'
import {
  shouldSkipUrl,
  markUrlCompleted,
  clearBulkCompletionRecords,
} from '../utils/bulkClientState.js'

const fileInput = ref(null)
const logBox = ref(null)
const selectedFile = ref(null)
const running = ref(false)
const skipCompleted = ref(true)
const delaySeconds = ref(2)

const total = ref(0)
const currentIndex = ref(0)
const okCount = ref(0)
const skipCount = ref(0)
const failCount = ref(0)
const logLines = ref([])
const abortController = ref(null)

const DEFAULT_FORMAT = 'bestvideo+bestaudio/best'

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

function pushLog(msg) {
  logLines.value = [...logLines.value, `[${new Date().toLocaleTimeString()}] ${msg}`]
  requestAnimationFrame(() => {
    const el = logBox.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function syncFile() {
  const f = fileInput.value?.files?.[0]
  selectedFile.value = f || null
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function parseFilenameFromResponse(response, fallback = 'video.mp4') {
  const cd = response.headers['content-disposition']
  if (!cd) return fallback
  const match = cd.match(/filename\*?=(?:UTF-8'')?([^;\n]+)/i)
  if (!match) return fallback
  try {
    return decodeURIComponent(match[1].replace(/"/g, ''))
  } catch {
    return fallback
  }
}

function triggerBrowserSave(response) {
  const filename = parseFilenameFromResponse(response)
  const blob = new Blob([response.data])
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  window.URL.revokeObjectURL(url)
  return filename
}

function clearLocalRecords() {
  if (running.value) return
  clearBulkCompletionRecords()
  pushLog('已清除本机「完成」记录（下次可重新下载相同链接）')
}

onMounted(() => {
  fileInput.value?.addEventListener('change', syncFile)
})
onBeforeUnmount(() => {
  fileInput.value?.removeEventListener('change', syncFile)
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
  abortController.value = new AbortController()
  const signal = abortController.value.signal

  pushLog(`开始解析：${file.name}`)

  try {
    const extracted = await bulkExtractUrls(file, signal)
    if (!extracted.success) {
      pushLog(`错误：${extracted.error || '解析失败'}`)
      return
    }
    const urls = extracted.urls || []
    total.value = urls.length
    if (!urls.length) {
      pushLog('文件中未识别到任何 http(s) 链接')
      return
    }
    pushLog(`共识别 ${urls.length} 条链接，将依次弹出浏览器下载（请勿关闭本页）`)

    for (let i = 0; i < urls.length; i++) {
      if (signal.aborted) {
        pushLog('已取消')
        break
      }
      const url = urls[i]
      const idx = i + 1
      currentIndex.value = idx
      const short = url.length > 72 ? `${url.slice(0, 72)}…` : url

      if (skipCompleted.value && shouldSkipUrl(url)) {
        skipCount.value += 1
        pushLog(`[${idx}/${urls.length}] 跳过 ${short}（本机记录）`)
      } else {
        try {
          const response = await downloadViaServer(url, DEFAULT_FORMAT, {
            deleteAfterSend: true,
            signal,
          })
          const filename = triggerBrowserSave(response)
          markUrlCompleted(url, filename, '')
          okCount.value += 1
          pushLog(`[${idx}/${urls.length}] 成功 ${short} → ${filename}`)
        } catch (e) {
          if (signal.aborted || e.name === 'CanceledError' || e.code === 'ERR_CANCELED') {
            pushLog('已取消')
            break
          }
          failCount.value += 1
          const msg = e.response?.data?.detail?.error || e.response?.data?.detail || e.message || '未知错误'
          pushLog(`[${idx}/${urls.length}] 失败 ${short} — ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`)
        }
      }

      if (i < urls.length - 1 && delaySeconds.value > 0 && !signal.aborted) {
        await sleep(Math.min(60, Math.max(0, delaySeconds.value)) * 1000)
      }
    }

    if (!signal.aborted) {
      pushLog(`全部结束：成功 ${okCount.value}，跳过 ${skipCount.value}，失败 ${failCount.value}`)
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      pushLog('已取消')
    } else {
      pushLog(`请求异常：${e.message || e}`)
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
