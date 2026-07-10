/**
 * YouTube 关键词搜索 + 阈值筛选；可选直接走 bulk SSE 下载。
 */

function parseSseDataBlocks(buffer, onDataObj) {
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  for (const block of parts) {
    for (const line of block.split('\n')) {
      if (line.startsWith('data:')) {
        const raw = line.slice(5).trim()
        try {
          onDataObj(JSON.parse(raw))
        } catch {
          /* ignore */
        }
      }
    }
  }
  return rest
}

/**
 * 仅搜索，不下载。
 * @param {{ query: string, maxResults?: number, minViews?: number, minLikes?: number, searchPool?: number }} opts
 */
export async function searchYoutube(opts = {}) {
  const {
    query,
    maxResults = 20,
    minViews = 0,
    minLikes = 0,
    searchPool,
    dateFilter = 'all',
    uploadDate = '',
  } = opts

  const body = {
    query,
    max_results: maxResults,
    min_views: minViews,
    min_likes: minLikes,
    date_filter: dateFilter || 'all',
  }
  if (searchPool != null && searchPool !== '') {
    body.search_pool = Number(searchPool)
  }
  if (body.date_filter === 'date' && uploadDate) {
    body.upload_date = uploadDate
  }

  const res = await fetch('/api/youtube/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  if (!res.ok) {
    let detail = ''
    try {
      const j = await res.json()
      detail = j.detail || j.message || ''
    } catch {
      detail = await res.text()
    }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * 搜索并批量下载（SSE）。
 * @param {object} options
 * @param {string} options.query
 * @param {number} [options.maxResults=20]
 * @param {number} [options.minViews=0]
 * @param {number} [options.minLikes=0]
 * @param {number} [options.searchPool]
 * @param {boolean} [options.skipCompleted=true]
 * @param {boolean} [options.verifyFile=true]
 * @param {number} [options.delaySeconds=2]
 * @param {boolean} [options.packForBrowser=true]
 * @param {AbortSignal} [options.signal]
 * @param {(obj: object) => void} options.onEvent
 */
export async function youtubeSearchDownloadStream(options = {}) {
  const {
    query,
    maxResults = 20,
    minViews = 0,
    minLikes = 0,
    searchPool,
    skipCompleted = true,
    verifyFile = true,
    formatId = 'bestvideo+bestaudio/best',
    delaySeconds = 2,
    downloadDir = '',
    packForBrowser = false,
    deliverFiles = true,
    dateFilter = 'all',
    uploadDate = '',
    onEvent = () => {},
    signal,
  } = options

  const form = new FormData()
  form.append('query', query)
  form.append('max_results', String(maxResults))
  form.append('min_views', String(minViews || 0))
  form.append('min_likes', String(minLikes || 0))
  if (searchPool != null && searchPool !== '') {
    form.append('search_pool', String(searchPool))
  }
  form.append('date_filter', dateFilter || 'all')
  if ((dateFilter || 'all') === 'date' && uploadDate) {
    form.append('upload_date', uploadDate)
  }
  form.append('skip_completed', skipCompleted ? 'true' : 'false')
  form.append('verify_file', verifyFile ? 'true' : 'false')
  form.append('format_id', formatId)
  form.append('delay_seconds', String(delaySeconds))
  form.append('download_dir', typeof downloadDir === 'string' ? downloadDir : '')
  form.append('pack_for_browser', packForBrowser ? 'true' : 'false')
  form.append('deliver_files', deliverFiles ? 'true' : 'false')

  const res = await fetch('/api/youtube/search-download', {
    method: 'POST',
    body: form,
    signal,
  })

  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }

  const reader = res.body?.getReader()
  if (!reader) {
    throw new Error('无法读取响应流')
  }

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = parseSseDataBlocks(buffer, onEvent)
    }
  } catch (e) {
    if (signal?.aborted) return
    throw e
  }
  if (buffer.trim()) {
    parseSseDataBlocks(buffer + '\n\n', onEvent)
  }
}
