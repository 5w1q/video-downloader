/**
 * 批量上传链接表，SSE 流式进度；可选 packForBrowser 完成后 GET ZIP 到本机。
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
 * @param {File} file
 * @param {object} options
 * @param {boolean} [options.skipCompleted=true]
 * @param {boolean} [options.verifyFile=true] 为 true 时仅当服务器上仍存在同名文件才跳过
 * @param {string} [options.formatId]
 * @param {number} [options.delaySeconds=2]
 * @param {string} [options.downloadDir]
 * @param {boolean} [options.packForBrowser=false]
 * @param {boolean} [options.deliverFiles=true]
 * @param {AbortSignal} [options.signal]
 * @param {(obj: object) => void} options.onEvent
 */
export async function bulkDownloadStream(file, options = {}) {
  const {
    skipCompleted = true,
    verifyFile = true,
    formatId = 'bestvideo+bestaudio/best',
    delaySeconds = 2,
    downloadDir = '',
    packForBrowser = false,
    deliverFiles = true,
    onEvent = () => {},
    signal,
  } = options

  const form = new FormData()
  form.append('file', file)
  form.append('skip_completed', skipCompleted ? 'true' : 'false')
  form.append('verify_file', verifyFile ? 'true' : 'false')
  form.append('format_id', formatId)
  form.append('delay_seconds', String(delaySeconds))
  form.append('download_dir', typeof downloadDir === 'string' ? downloadDir : '')
  form.append('pack_for_browser', packForBrowser ? 'true' : 'false')
  form.append('deliver_files', deliverFiles ? 'true' : 'false')

  const res = await fetch('/api/bulk-download', {
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

/**
 * 按 URL 列表批量下载（SSE），用于预览结果后直接下载。
 * @param {string[]} urls
 * @param {object} options
 * @param {string[]} [options.titles] 与 urls 等长的标题（命名回退）
 * @param {string[]} [options.downloadUrls] 与 urls 等长的直链（如 IG CDN）
 */
export async function bulkDownloadUrlsStream(urls, options = {}) {
  const {
    skipCompleted = true,
    verifyFile = true,
    formatId = 'bestvideo+bestaudio/best',
    delaySeconds = 2,
    downloadDir = '',
    packForBrowser = false,
    deliverFiles = true,
    sourceName = 'preview',
    titles,
    downloadUrls,
    onEvent = () => {},
    signal,
  } = options

  const payload = {
    urls,
    skip_completed: skipCompleted,
    verify_file: verifyFile,
    format_id: formatId,
    delay_seconds: delaySeconds,
    download_dir: typeof downloadDir === 'string' ? downloadDir : '',
    pack_for_browser: packForBrowser,
    deliver_files: deliverFiles,
    source_name: sourceName,
  }
  if (Array.isArray(titles) && titles.length === urls.length) {
    payload.titles = titles.map((t) => (typeof t === 'string' ? t : String(t || '')))
  }
  if (Array.isArray(downloadUrls) && downloadUrls.length === urls.length) {
    payload.download_urls = downloadUrls.map((u) =>
      typeof u === 'string' && /^https?:\/\//i.test(u) ? u : ''
    )
  }

  const res = await fetch('/api/bulk-download/urls', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
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
