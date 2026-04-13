<template>
  <div class="min-h-screen flex flex-col bg-bg-main">
    <AppHeader
      :user="currentUser"
      @login="showAuthModal('login')"
      @register="showAuthModal('register')"
      @logout="handleLogout"
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
                :key="summaryKey"
                @loading-change="handleSummarizeLoadingChange"
                @need-login="showAuthModal('login')"
              />
            </div>
          </div>
        </div>
      </section>
      <BulkDownloadSection />
      <FeatureSection />
      <HowToSection />
      <ComparisonSection />
      <PlatformSection />
    </main>
    <AppFooter />

    <AuthModal
      :visible="authModalVisible"
      :initialMode="authModalMode"
      @close="authModalVisible = false"
      @success="handleAuthSuccess"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import AppHeader from './components/AppHeader.vue'
import HeroSection from './components/HeroSection.vue'
import VideoResult from './components/VideoResult.vue'
import VideoSummary from './components/VideoSummary.vue'
import BulkDownloadSection from './components/BulkDownloadSection.vue'
import FeatureSection from './components/FeatureSection.vue'
import HowToSection from './components/HowToSection.vue'
import ComparisonSection from './components/ComparisonSection.vue'
import PlatformSection from './components/PlatformSection.vue'
import AppFooter from './components/AppFooter.vue'
import AuthModal from './components/AuthModal.vue'
import { parseVideo, downloadViaServer } from './api/video.js'
import { getSavedUser, fetchMe, logout as logoutApi, isLoggedIn } from './api/auth.js'
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

onMounted(() => {
  document.addEventListener('keydown', onKeyDown)
  restoreUser()
})
onBeforeUnmount(() => { document.removeEventListener('keydown', onKeyDown) })

// ===== 用户状态管理 =====
const currentUser = ref(null)
const authModalVisible = ref(false)
const authModalMode = ref('login')

function showAuthModal(mode = 'login') {
  authModalMode.value = mode
  authModalVisible.value = true
}

function handleAuthSuccess(user) {
  currentUser.value = user
}

function handleLogout() {
  logoutApi()
  currentUser.value = null
}

async function restoreUser() {
  if (!isLoggedIn()) return
  const saved = getSavedUser()
  if (saved) currentUser.value = saved
  try {
    currentUser.value = await fetchMe()
  } catch {
    handleLogout()
  }
}

// ===== 视频功能 =====
const loading = ref(false)
const downloading = ref(false)
const videoData = ref(null)
const currentUrl = ref('')
const summaryKey = ref(0)
const summarizing = ref(false)

function handleSummarize() {
  summaryKey.value++
}

function handleSummarizeLoadingChange(isLoading) {
  summarizing.value = isLoading
}

async function handleParse(url) {
  loading.value = true
  videoData.value = null
  currentUrl.value = url
  try {
    const res = await parseVideo(url)
    if (res.success) {
      videoData.value = res.data
      summaryKey.value++
    } else {
      alert('解析失败：' + (res.error || '未知错误'))
    }
  } catch (err) {
    const d = err.response?.data?.detail
    const msg =
      (typeof d === 'object' && d !== null && d.error) ||
      (typeof d === 'string' ? d : null) ||
      err.message ||
      '未知错误'
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
      const match = contentDisposition.match(/filename\*?=(?:UTF-8'')?([^;\n]+)/i)
      if (match) filename = decodeURIComponent(match[1].replace(/"/g, ''))
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
