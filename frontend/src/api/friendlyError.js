/**
 * 将浏览器 / 网络侧英文异常转成中文（后端 SSE 已做友好化，此处兜底）。
 * @param {unknown} err
 * @param {string} [fallback='操作失败，请稍后重试']
 */
export function friendlyError(err, fallback = '操作失败，请稍后重试') {
  const raw = (err && typeof err === 'object' && 'message' in err
    ? String(err.message || '')
    : String(err || '')).trim()
  if (!raw) return fallback
  if (/[\u4e00-\u9fff]/.test(raw) && !/[A-Za-z]{6,}/.test(raw)) return raw

  const low = raw.toLowerCase()
  if (low.includes('failed to fetch') || low.includes('networkerror') || low.includes('load failed')) {
    return '网络请求失败，请检查后端是否启动或网络连接后重试。'
  }
  if (low.includes('aborted') || low.includes('aborterror')) {
    return '请求已取消。'
  }
  if (low.includes('timeout') || low.includes('timed out')) {
    return '请求超时，请稍后重试。'
  }
  if (low.includes('ssl') || low.includes('unexpected_eof') || low.includes('certificate')) {
    return 'SSL 连接异常，请检查网络或代理后重试。'
  }
  if (/^http\s*\d{3}/i.test(raw) || /status\s*code/i.test(low)) {
    return '服务器响应异常，请稍后重试。'
  }
  if (/[\u4e00-\u9fff]/.test(raw)) {
    // 中文前缀 + 英文尾：只保留中文前缀语义
    const m = raw.match(/^([^:：]{1,80})[:：]\s*/)
    if (m && /[\u4e00-\u9fff]/.test(m[1])) {
      return `${m[1].replace(/[:：]\s*$/, '')}：请稍后重试。`
    }
    return raw
  }
  return fallback
}
