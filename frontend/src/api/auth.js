import axios from 'axios'

const USER_KEY = 'saveany_user'

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

function currentNext() {
  return window.location.pathname + window.location.search + window.location.hash
}

export function goToAbLogin(mode = 'login') {
  const next = encodeURIComponent(currentNext())
  const endpoint = mode === 'register' ? '/api/auth/register' : '/api/auth/login'
  window.location.href = `${endpoint}?next=${next}`
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
  removeLocalUser()
}

export function isLoggedIn() {
  return !!getSavedUser()
}
