<template>
  <header class="sticky top-0 z-50 bg-white/80 backdrop-blur-md border-b border-border-light">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <a href="/" class="flex items-center gap-3" title="SaveAny - 免费在线万能视频下载器">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-primary to-blue-600 flex items-center justify-center shadow-sm" role="img" aria-label="SaveAny Logo">
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>
        <span class="text-lg font-semibold text-text-primary tracking-tight">SaveAny</span>
        <span class="hidden sm:inline text-xs text-text-muted bg-primary-light px-2 py-0.5 rounded-full">万能视频下载</span>
      </a>
      <nav class="hidden md:flex items-center gap-6 text-sm text-text-secondary" aria-label="主导航">
        <a href="#bulk-download" class="hover:text-primary transition-colors" title="表格批量下载">批量下载</a>
        <a href="#features" class="hover:text-primary transition-colors" title="查看SaveAny功能特性">功能特性</a>
        <a href="#how-to-use" class="hover:text-primary transition-colors" title="了解如何使用SaveAny下载视频">使用教程</a>
        <a href="#comparison" class="hover:text-primary transition-colors" title="SaveAny与其他工具对比">工具对比</a>
      </nav>
      <div class="flex items-center gap-3">
        <!-- 会话校验中：不占位「登录」以免先闪一下再变成已登录 -->
        <template v-if="authChecking && !user">
          <div class="hidden sm:flex items-center gap-2" aria-busy="true" aria-label="加载登录状态">
            <span class="inline-block h-9 w-[11rem] rounded-full bg-gray-100 animate-pulse" />
          </div>
        </template>

        <!-- 未登录 -->
        <template v-else-if="!user">
          <button @click="$emit('login')" class="hidden sm:inline-flex items-center px-4 py-2 rounded-full text-sm font-medium text-text-secondary hover:text-primary hover:bg-gray-50 transition-colors cursor-pointer">
            登录
          </button>
          <button @click="$emit('register')" class="hidden sm:inline-flex items-center gap-1.5 px-4 py-2 rounded-full text-sm font-medium text-white bg-primary hover:bg-blue-600 transition-colors shadow-sm cursor-pointer">
            免费注册
          </button>
        </template>

        <!-- 已登录：显示用户名、会员状态、剩余积分 -->
        <template v-else>
          <div class="hidden sm:flex items-center text-sm text-text-secondary">
            <span class="font-medium text-text-primary">{{ displayName }}</span>
            <span class="mx-2 text-text-muted">·</span>
            <span :class="user?.is_vip ? 'text-primary font-medium' : 'text-text-secondary'">
              {{ user?.is_vip ? 'VIP' : '免费用户' }}
            </span>
            <span class="mx-2 text-text-muted">·</span>
            <span>积分 {{ safeCredits }}</span>
            <button
              @click="$emit('logout')"
              class="ml-3 text-text-muted hover:text-primary transition-colors cursor-pointer"
            >
              退出
            </button>
          </div>
          <div class="sm:hidden flex items-center gap-2 text-xs text-text-secondary">
            {{ user?.is_vip ? 'VIP' : '免费' }} · {{ safeCredits }} 积分
            <button @click="$emit('logout')" class="text-text-muted hover:text-primary transition-colors cursor-pointer">
              退出
            </button>
          </div>
        </template>
      </div>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  user: { type: Object, default: null },
  /** 首次拉取 /api/auth/me 完成前为 true */
  authChecking: { type: Boolean, default: false },
})

defineEmits(['login', 'register', 'logout'])

const displayName = computed(() => {
  const u = props.user || {}
  const name = (u.display_name || u.username || '').trim()
  if (name) return name
  const email = (u.email || '').trim()
  return email ? email.split('@')[0] : '用户'
})

const safeCredits = computed(() => {
  const v = Number(props.user?.credits ?? 0)
  if (Number.isNaN(v) || v < 0) return 0
  return Math.floor(v)
})
</script>
