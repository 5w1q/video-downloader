/**
 * 批量上传链接表，SSE 流式进度（文件保存在服务端 downloads 目录）
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
 * @param {AbortSignal} [options.signal]
 * @param {(obj: object) => void} options.onEvent 每条 SSE JSON（event: start|item|done|error）
 */
export async function bulkDownloadStream(file, options = {}) {
  const {
    skipCompleted = true,
    verifyFile = true,
    formatId = 'bestvideo+bestaudio/best',
    delaySeconds = 2,
    onEvent = () => {},
    signal,
  } = options

  const form = new FormData()
  form.append('file', file)
  form.append('skip_completed', skipCompleted ? 'true' : 'false')
  form.append('verify_file', verifyFile ? 'true' : 'false')
  form.append('format_id', formatId)
  form.append('delay_seconds', String(delaySeconds))

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
