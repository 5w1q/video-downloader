<template>
  <div class="min-h-screen flex flex-col bg-bg-main">
    <AppHeader
      :user="currentUser"
      :auth-checking="authChecking"
      :auth-redirect-gate="authGate"
      @login="goToAbLogin('login')"
      @register="goToAbLogin('register')"
    />
    <main class="flex-1">
      <HeroSection
        @parse="handleParse"
        :loading="loading"
        :compact="!!videoData"
        :showSlogan="!videoData || demoMode"
      />
      <!-- 视频信息 + AI 总结：左右双栏同屏布局 -->
      <section v-if="videoData" class="py-4 sm:py-6 bg-white">
        <div class="max-w-7xl mx-auto px-4 sm:px-6">
          <div class="flex flex-col lg:flex-row gap-6">
            <!-- 左栏：视频信息 -->
            <div class="w-full lg:w-2/5 lg:flex-shrink-0">
              <VideoResult
                :video="videoData"
                :downloading="downloading"
                :summarizing="summarizing"
                @download="handleDownload"
                @summarize="handleSummarize"
              />
            </div>
            <!-- 右栏：AI 总结 -->
            <div class="w-full lg:w-3/5 min-w-0">
              <VideoSummary
                :videoUrl="currentUrl"
                :videoTitle="videoData.title"
                :user="currentUser"
                :summarizeTrigger="summaryKey"
                @loading-change="handleSummarizeLoadingChange"
                @need-login="goToAbLogin('login')"
                @show-pricing="scrollToPricing"
                @wallet-sync="syncWalletFromAb"
              />
            </div>
          </div>
        </div>
      </section>
      <KeywordDownloadSection />
      <FeatureSection />
      <HowToSection />
      <ComparisonSection />
      <PlatformSection />
    </main>
    <AppFooter />
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import AppHeader from './components/AppHeader.vue'
import HeroSection from './components/HeroSection.vue'
import VideoResult from './components/VideoResult.vue'
import VideoSummary from './components/VideoSummary.vue'
import KeywordDownloadSection from './components/KeywordDownloadSection.vue'
import FeatureSection from './components/FeatureSection.vue'
import HowToSection from './components/HowToSection.vue'
import ComparisonSection from './components/ComparisonSection.vue'
import PlatformSection from './components/PlatformSection.vue'
import AppFooter from './components/AppFooter.vue'
import { parseVideo, downloadViaServer } from './api/video.js'
import {
  fetchMe,
  goToAbLogin,
  loginGateEnabled,
  markAuthFresh,
  wipeClientAuthCache,
  isLocalAuthExpired,
  buildMainSiteLoginUrl,
} from './api/auth.js'

const authGate = loginGateEnabled()
const demoMode = ref(false)
let enterCount = 0
let enterTimer = null

function onKeyDown(e) {
  if (e.key === 'Enter' && !e.target.matches('input, textarea, [contenteditable]')) {
    enterCount++
    clearTimeout(enterTimer)
    if (enterCount >= 3) {
      demoMode.value = !demoMode.value
      enterCount = 0
    } else {
      enterTimer = setTimeout(() => { enterCount = 0 }, 800)
    }
  }
}

// ===== 用户状态管理 =====
const currentUser = ref(null)
/** 首屏不向顶栏注入 localStorage 里的过时 credits；在校验 /api/auth/me 完成前显示骨架 */
const authChecking = ref(true)

async function restoreUser() {
  authChecking.value = true
  try {
    if (authGate && isLocalAuthExpired()) {
      wipeClientAuthCache()
      window.location.replace(buildMainSiteLoginUrl('login'))
      return
    }
    try {
      currentUser.value = await fetchMe()
      if (currentUser.value && authGate) markAuthFresh()
    } catch (e) {
      currentUser.value = null
      if (authGate && e?.response?.status === 401) {
        wipeClientAuthCache()
        window.location.replace(buildMainSiteLoginUrl('login'))
        return
      }
    }
    if (authGate && !currentUser.value) {
      wipeClientAuthCache()
      window.location.replace(buildMainSiteLoginUrl('login'))
    }
  } finally {
    authChecking.value = false
  }
}

/** AI 总结 SSE / 流结束：始终以 Ab 为准拉取余额，不再先做乐观补丁（避免先闪错误整数再对齐） */
async function syncWalletFromAb() {
  if (!currentUser.value) return
  try {
    currentUser.value = await fetchMe()
  } catch (e) {
    if (authGate && e?.response?.status === 401) {
      currentUser.value = null
      wipeClientAuthCache()
      window.location.replace(buildMainSiteLoginUrl('login'))
      return
    }
  }
}

/** 与 Ab 门户一致：定时 + 回到页签时同步积分（跨标签扣费、其它入口消费均可对齐） */
const WALLET_POLL_MS = 5000
let walletPollTimer = null

async function pullWalletFromAb() {
  if (!currentUser.value) return
  try {
    currentUser.value = await fetchMe()
  } catch (e) {
    if (authGate && e?.response?.status === 401) {
      currentUser.value = null
      wipeClientAuthCache()
      window.location.replace(buildMainSiteLoginUrl('login'))
    }
  }
}

function startWalletPolling() {
  if (walletPollTimer != null) return
  walletPollTimer = setInterval(() => {
    pullWalletFromAb()
  }, WALLET_POLL_MS)
}

function stopWalletPolling() {
  if (walletPollTimer != null) {
    clearInterval(walletPollTimer)
    walletPollTimer = null
  }
}

function onWalletVisibility() {
  if (document.visibilityState === 'visible') pullWalletFromAb()
}

watch(
  currentUser,
  (u) => {
    if (u) startWalletPolling()
    else stopWalletPolling()
  },
  { immediate: true },
)

onMounted(() => {
  document.addEventListener('keydown', onKeyDown)
  restoreUser()
  document.addEventListener('visibilitychange', onWalletVisibility)
  window.addEventListener('focus', pullWalletFromAb)
})
onBeforeUnmount(() => {
  stopWalletPolling()
  document.removeEventListener('keydown', onKeyDown)
  document.removeEventListener('visibilitychange', onWalletVisibility)
  window.removeEventListener('focus', pullWalletFromAb)
})

// ===== 视频功能 =====
const loading = ref(false)
const downloading = ref(false)
const videoData = ref(null)
const currentUrl = ref('')
const summaryKey = ref(0)
const summarizing = ref(false)

function handleSummarize() {
  // 解除左侧「AI 总结」按钮的 disabled（:disabled="summarizing"），否则上次卡住时点击无任何事件
  summarizing.value = false
  summaryKey.value++
}

function handleSummarizeLoadingChange(isLoading) {
  summarizing.value = isLoading
}

function scrollToPricing() {
  document.getElementById('pricing')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function handleParse(url) {
  loading.value = true
  videoData.value = null
  currentUrl.value = url
  try {
    const res = await parseVideo(url)
    if (res.success) {
      videoData.value = res.data
    } else {
      alert('解析失败：' + (res.error || '未知错误'))
    }
  } catch (err) {
    const msg = err.response?.data?.detail?.error || err.response?.data?.detail || err.message
    alert('解析失败：' + msg)
  } finally {
    loading.value = false
  }
}

async function handleDownload(formatId) {
  downloading.value = true
  try {
    const response = await downloadViaServer(currentUrl.value, formatId)
    const contentDisposition = response.headers['content-disposition']
    let filename = 'video.mp4'
    if (contentDisposition) {
      // 优先 RFC 5987：filename*=UTF-8''...（中文标题）
      const star = contentDisposition.match(/filename\*\s*=\s*UTF-8''([^;\n]+)/i)
      const plain = contentDisposition.match(/filename\s*=\s*"?([^";\n]+)"?/i)
      const raw = star?.[1] || plain?.[1]
      if (raw) {
        try {
          filename = decodeURIComponent(raw.replace(/"/g, '').trim())
        } catch {
          filename = raw.replace(/"/g, '').trim()
        }
      }
    }
    const blob = new Blob([response.data])
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    window.URL.revokeObjectURL(url)
  } catch (err) {
    alert('下载失败：' + (err.message || '请稍后重试'))
  } finally {
    downloading.value = false
  }
}
</script>
