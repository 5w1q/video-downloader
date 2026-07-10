import axios from 'axios'

const USER_KEY = 'saveany_user'
/** 本地会话有效期截止（毫秒时间戳）；仅用于子站「最长保留登录态」判断，与 Ab Cookie 无关 */
const SESSION_EXPIRES_AT_KEY = 'saveany_auth_expires_at'
const SESSION_MAX_MS = 3 * 24 * 60 * 60 * 1000

const DEFAULT_AB_LOGIN_PAGE = 'https://sayhi-ab.asia/login.html'

function removeLocalUser() {
  localStorage.removeItem(USER_KEY)
}

export function getSavedUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function saveUser(user) {
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

/** 生产构建默认开启：打开子站未登录则跳转主站登录页；本地设 VITE_AB_LOGIN_SKIP=1 可关闭 */
export function loginGateEnabled() {
  if (import.meta.env.VITE_AB_LOGIN_SKIP === '1') return false
  return import.meta.env.PROD
}

export function clearAuthSessionTracking() {
  localStorage.removeItem(SESSION_EXPIRES_AT_KEY)
}

export function wipeClientAuthCache() {
  removeLocalUser()
  clearAuthSessionTracking()
}

/** 登录校验成功后调用：从此时起本地视为最多保留 3 天（期满须重新打开主站登录页） */
export function markAuthFresh() {
  localStorage.setItem(SESSION_EXPIRES_AT_KEY, String(Date.now() + SESSION_MAX_MS))
}

export function isLocalAuthExpired() {
  const raw = localStorage.getItem(SESSION_EXPIRES_AT_KEY)
  if (!raw) return false
  const t = parseInt(raw, 10)
  if (!Number.isFinite(t)) return true
  return Date.now() > t
}

/**
 * 主站登录页完整 URL（含登录后回到当前子站）
 * @param {'login'|'register'} mode
 */
export function buildMainSiteLoginUrl(mode = 'login') {
  const base = (import.meta.env.VITE_AB_LOGIN_PAGE_URL || DEFAULT_AB_LOGIN_PAGE).trim().replace(/\/$/, '')
  const origin = (import.meta.env.VITE_VIDEO_APP_ORIGIN || window.location.origin).trim().replace(/\/$/, '')
  const next = encodeURIComponent(`${origin}/`)
  const sep = base.includes('?') ? '&' : '?'
  let url = `${base}${sep}next=${next}`
  if (mode === 'register') url += '&mode=register'
  return url
}

export function goToAbLogin(mode = 'login') {
  window.location.href = buildMainSiteLoginUrl(mode)
}

export async function register() {
  goToAbLogin('register')
  return null
}

export async function login() {
  goToAbLogin('login')
  return null
}

export async function fetchMe() {
  const res = await axios.get('/api/auth/me', { withCredentials: true })
  const user = res.data.data
  saveUser(user)
  return user
}

export async function logout() {
  try {
    await axios.post('/api/auth/logout', {}, { withCredentials: true })
  } catch {
    // ignore
  }
  wipeClientAuthCache()
}

export function isLoggedIn() {
  return !!getSavedUser()
}
