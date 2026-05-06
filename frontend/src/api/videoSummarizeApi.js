/**
 * AI 视频总结 API 封装（fetch + SSE）
 *
 * 注意：在浏览器「网络」里请找 POST /api/summarize，不要与本文件名混淆；
 * DevTools 里若筛选 summarize，容易误点到本文件的 GET（Vite 加载源码）。
 */

async function handleSSEStream(response, callbacks) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''
  let dataLines = []
  let hasData = false
  let sawTerminal = false

  function dispatch() {
    if (!hasData) {
      dataLines = []
      currentEvent = ''
      return
    }
    const ev = currentEvent || 'message'
    if (ev === 'done' || ev === 'error') {
      sawTerminal = true
    }
    const handler = callbacks[ev]
    if (handler) handler(dataLines.join('\n'))
    dataLines = []
    hasData = false
    currentEvent = ''
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (value) {
        buffer += decoder.decode(value, { stream: !done })
      }
      if (done) {
        buffer += decoder.decode()
        break
      }

      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '')
        if (line === '') {
          dispatch()
          continue
        }

        if (line.startsWith(':')) continue

        const colonIdx = line.indexOf(':')
        if (colonIdx < 0) continue

        const field = line.slice(0, colonIdx)
        let val = line.slice(colonIdx + 1)
        if (val.startsWith(' ')) val = val.slice(1)
        val = val.replace(/\r$/, '')

        if (field === 'event') {
          currentEvent = val
        } else if (field === 'data') {
          hasData = true
          dataLines.push(val)
        }
      }
    }
    if (buffer) {
      const lines = buffer.split('\n')
      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '')
        if (line === '') {
          dispatch()
          continue
        }
        if (line.startsWith(':')) continue
        const colonIdx = line.indexOf(':')
        if (colonIdx < 0) continue
        const field = line.slice(0, colonIdx)
        let val = line.slice(colonIdx + 1)
        if (val.startsWith(' ')) val = val.slice(1)
        val = val.replace(/\r$/, '')
        if (field === 'event') {
          currentEvent = val
        } else if (field === 'data') {
          hasData = true
          dataLines.push(val)
        }
      }
      buffer = ''
    }
    if (hasData) {
      dispatch()
    }
  } catch (e) {
    if (!sawTerminal && callbacks.error) {
      callbacks.error(
        JSON.stringify({
          message: `读取总结流失败：${e?.message || String(e)}`,
        }),
      )
      sawTerminal = true
    }
  } finally {
    dispatch()
    if (!sawTerminal && callbacks.error) {
      callbacks.error(
        JSON.stringify({
          message:
            '连接已结束，但未收到完成事件（可能为网络中断、代理超时或后端异常）。请刷新后重试。',
        }),
      )
    }
  }
}

export async function summarizeVideo(url, language = 'zh', callbacks = {}) {
  const response = await fetch('/api/summarize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ url, language }),
  })

  if (!response.ok) {
    throw new Error(`请求失败: ${response.status}`)
  }

  await handleSSEStream(response, callbacks)
}

export async function chatWithVideo(url, question, subtitleText = '', callbacks = {}) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ url, question, subtitle_text: subtitleText }),
  })

  if (!response.ok) {
    throw new Error(`请求失败: ${response.status}`)
  }

  await handleSSEStream(response, callbacks)
}
