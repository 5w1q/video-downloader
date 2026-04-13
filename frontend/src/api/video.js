import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

/** 解析 / 拉直链常走 yt-dlp，冷站或反爬重试可能超过 2 分钟 */
const PARSE_TIMEOUT_MS = 600000

export async function parseVideo(url) {
  const { data } = await api.post('/parse', { url }, { timeout: PARSE_TIMEOUT_MS })
  return data
}

export async function getDirectUrl(url, formatId) {
  const { data } = await api.post(
    '/direct-url',
    { url, format_id: formatId },
    { timeout: PARSE_TIMEOUT_MS }
  )
  return data
}

export function getDownloadUrl() {
  return '/api/download'
}

export async function downloadViaServer(url, formatId) {
  const response = await api.post(
    '/download',
    { url, format_id: formatId },
    { responseType: 'blob', timeout: 600000 }
  )
  return response
}
