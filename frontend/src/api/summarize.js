/**
 * AI 视频总结 API 封装（SSE，与 FastAPI EventSourceResponse 块格式一致）
 */

import { getToken } from './auth'
import { consumeFetchSse } from '../utils/parseSseStream.js'

function authHeaders() {
  const token = getToken()
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

export async function summarizeVideo(url, language = 'zh', callbacks = {}, meta = {}) {
  const response = await fetch('/api/summarize', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({
      url,
      language,
      title: meta.title || '',
      description: meta.description || '',
    }),
  })

  if (!response.ok) {
    const t = await response.text().catch(() => '')
    throw new Error(t || `请求失败: ${response.status}`)
  }

  await consumeFetchSse(response, (ev, data) => {
    if (ev === 'subtitle' && callbacks.subtitle) callbacks.subtitle(data)
    else if (ev === 'summary' && callbacks.summary) callbacks.summary(data)
    else if (ev === 'mindmap' && callbacks.mindmap) callbacks.mindmap(data)
    else if (ev === 'quota' && callbacks.quota) callbacks.quota(data)
    else if (ev === 'done' && callbacks.done) callbacks.done(data)
    else if (ev === 'error' && callbacks.error) callbacks.error(data)
  })
}

export async function chatWithVideo(url, question, subtitleText = '', callbacks = {}, meta = {}) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({
      url,
      question,
      subtitle_text: subtitleText,
      title: meta.title || '',
      description: meta.description || '',
    }),
  })

  if (!response.ok) {
    const t = await response.text().catch(() => '')
    throw new Error(t || `请求失败: ${response.status}`)
  }

  await consumeFetchSse(response, (ev, data) => {
    if (ev === 'answer' && callbacks.answer) callbacks.answer(data)
    else if (ev === 'done' && callbacks.done) callbacks.done(data)
    else if (ev === 'error' && callbacks.error) callbacks.error(data)
  })
}
