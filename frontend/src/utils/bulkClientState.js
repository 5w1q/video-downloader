/** 批量下载「跳过已下」：记录在本机 localStorage，不依赖服务器 downloads 目录 */

const STORAGE_KEY = 'video-downloader-bulk-state'
const VERSION = 1

export function urlStateKey(raw) {
  const u = String(raw || '').trim().replace(/[).,;!?]+$/, '')
  try {
    const parsed = new URL(u)
    const protocol = parsed.protocol.toLowerCase()
    const host = (parsed.hostname || '').toLowerCase()
    let path = parsed.pathname || '/'
    if (path.length > 1 && path.endsWith('/')) path = path.slice(0, -1)
    if (!path) path = '/'
    return `${protocol}//${host}${path}${parsed.search}`
  } catch {
    return u
  }
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { version: VERSION, entries: {} }
    const data = JSON.parse(raw)
    if (!data || typeof data.entries !== 'object') return { version: VERSION, entries: {} }
    return { version: VERSION, entries: data.entries }
  } catch {
    return { version: VERSION, entries: {} }
  }
}

function saveState(state) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
}

export function shouldSkipUrl(url) {
  const key = urlStateKey(url)
  const { entries } = loadState()
  const rec = entries[key]
  if (!rec || typeof rec !== 'object') return false
  const fn = (rec.filename || '').trim()
  return Boolean(fn)
}

export function markUrlCompleted(url, filename, title) {
  const key = urlStateKey(url)
  const state = loadState()
  state.entries[key] = {
    filename: filename || '',
    title: title || '',
    completed_at: new Date().toISOString(),
  }
  saveState(state)
}

export function clearBulkCompletionRecords() {
  localStorage.removeItem(STORAGE_KEY)
}
