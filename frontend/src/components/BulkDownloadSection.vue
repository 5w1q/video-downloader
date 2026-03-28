<template>
  <section id="bulk-download" class="py-12 sm:py-16 bg-bg-section border-t border-border-light" aria-labelledby="bulk-heading">
    <div class="max-w-3xl mx-auto px-4 sm:px-6">
      <div class="text-center mb-8">
        <h2 id="bulk-heading" class="text-2xl sm:text-3xl font-bold text-text-primary mb-2">
          表格批量下载
        </h2>
        <p class="text-text-secondary text-sm sm:text-base max-w-xl mx-auto">
          上传 Excel（.xlsx）、CSV 或 TXT，自动识别单元格中的链接并顺序下载到
          <strong class="text-text-primary">服务器</strong>
          的下载目录；已成功的链接可自动跳过（可选校验磁盘文件是否存在）。
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
            <span class="text-text-secondary">跳过已下载过的链接</span>
          </label>
          <label class="inline-flex items-center gap-2 cursor-pointer select-none">
            <input v-model="verifyFile" type="checkbox" :disabled="running || !skipCompleted" class="rounded border-border text-primary focus:ring-primary/30" />
            <span class="text-text-secondary">仅当服务器上仍存在文件时才跳过</span>
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
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { bulkDownloadStream } from '../api/bulk.js'

const fileInput = ref(null)
const logBox = ref(null)
const selectedFile = ref(null)
const running = ref(false)
const skipCompleted = ref(true)
const verifyFile = ref(true)
const delaySeconds = ref(2)

const total = ref(0)
const currentIndex = ref(0)
const okCount = ref(0)
const skipCount = ref(0)
const failCount = ref(0)
const logLines = ref([])
const abortController = ref(null)

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
  abortController.value = new AbortController()

  pushLog(`开始上传：${file.name}`)

  try {
    await bulkDownloadStream(file, {
      signal: abortController.value.signal,
      skipCompleted: skipCompleted.value,
      verifyFile: verifyFile.value,
      delaySeconds: delaySeconds.value,
      onEvent: (data) => {
        if (abortController.value?.signal.aborted) return
        const ev = data.event
        if (ev === 'start') {
          total.value = data.total || 0
          pushLog(`共识别 ${data.total} 条链接（来源：${data.source_name || file.name}）`)
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
        } else if (ev === 'error') {
          pushLog(`错误：${data.message || '未知错误'}`)
        }
      },
    })
  } catch (e) {
    if (e.name === 'AbortError') {
      pushLog('已取消（连接已断开，服务端可能仍在处理当前这一条）')
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
