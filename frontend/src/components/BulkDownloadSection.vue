<template>
  <section id="bulk-download" class="py-12 sm:py-16 bg-bg-section border-t border-border-light" aria-labelledby="bulk-heading">
    <div class="max-w-3xl mx-auto px-4 sm:px-6">
      <div class="text-center mb-8">
        <h2 id="bulk-heading" class="text-2xl sm:text-3xl font-bold text-text-primary mb-2">
          表格批量下载
        </h2>
        <p class="text-text-secondary text-sm sm:text-base max-w-xl mx-auto">
          上传 Excel（.xlsx）、CSV 或 TXT，识别链接后由服务器拉取视频，<strong class="text-text-primary">保存到您本机</strong>。
          在 <strong class="text-text-primary">Chrome / Edge（HTTPS）</strong> 下可<strong class="text-text-primary">只选一次文件夹</strong>，后续文件自动写入，无需每条都点「另存为」。
          若浏览器不支持或您取消了选文件夹，将退回为逐条下载提示。服务端传完后会删除临时文件。
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

        <label v-if="folderPickerSupported" class="flex items-start gap-2 text-sm cursor-pointer select-none">
          <input
            v-model="preferFolderPicker"
            type="checkbox"
            :disabled="running"
            class="mt-0.5 rounded border-border text-primary focus:ring-primary/30"
          />
          <span class="text-text-secondary">优先使用「选择文件夹」批量写入（推荐，仅 HTTPS 且浏览器支持时有效）</span>
        </label>

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

const folderPickerSupported = typeof window !== 'undefined' && typeof window.showDirectoryPicker === 'function'
const preferFolderPicker = ref(true)

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

function sanitizeBaseName(name) {
  const s = (name || 'video.mp4').replace(/[/\\?%*:|"<>]/g, '_').trim() || 'video.mp4'
  return s.length > 160 ? s.slice(0, 160) : s
}

function triggerBrowserSave(response) {
  const filename = sanitizeBaseName(parseFilenameFromResponse(response))
  const blob = new Blob([response.data])
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  window.URL.revokeObjectURL(url)
  return filename
}

async function saveResponseToDirectory(dirHandle, response, index, totalCount) {
  const fromHeader = parseFilenameFromResponse(response, `video_${index}.mp4`)
  const base = sanitizeBaseName(fromHeader)
  const prefix = `${String(index).padStart(Math.max(3, String(totalCount).length), '0')}_`
  const name = prefix + base
  const blob = response.data instanceof Blob ? response.data : new Blob([response.data])
  const buf = await blob.arrayBuffer()
  const fh = await dirHandle.getFileHandle(name, { create: true })
  const writable = await fh.createWritable()
  try {
    await writable.write(buf)
  } finally {
    await writable.close()
  }
  return name
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

    let dirHandle = null
    if (folderPickerSupported && preferFolderPicker.value) {
      pushLog('请在浏览器弹窗中选择一个文件夹（全部文件将写入该目录，无需每条确认）')
      try {
        dirHandle = await window.showDirectoryPicker({ mode: 'write' })
        pushLog('已选择文件夹，开始逐条拉取并写入…')
      } catch (e) {
        if (e.name === 'AbortError') {
          pushLog('未选择文件夹，改为逐条触发浏览器下载（部分浏览器可能需多次允许）')
        } else {
          pushLog(`无法使用文件夹 API（${e.message || e}），改为逐条下载`)
        }
        dirHandle = null
      }
    } else {
      pushLog(`共 ${urls.length} 条链接：将逐条下载到本机（关闭「优先选择文件夹」可改用传统另存为）`)
    }

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
          pushLog(`[${idx}/${urls.length}] 拉取中 ${short}…`)
          const response = await downloadViaServer(url, DEFAULT_FORMAT, {
            deleteAfterSend: true,
            signal,
          })
          let savedName
          if (dirHandle) {
            savedName = await saveResponseToDirectory(dirHandle, response, idx, urls.length)
          } else {
            savedName = triggerBrowserSave(response)
          }
          markUrlCompleted(url, savedName, '')
          okCount.value += 1
          pushLog(`[${idx}/${urls.length}] 成功 → ${savedName}`)
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
