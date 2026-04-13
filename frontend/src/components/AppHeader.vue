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
        <!-- 未登录 -->
        <template v-if="!user">
          <button @click="$emit('login')" class="hidden sm:inline-flex items-center px-4 py-2 rounded-full text-sm font-medium text-text-secondary hover:text-primary hover:bg-gray-50 transition-colors cursor-pointer">
            登录
          </button>
          <button @click="$emit('register')" class="hidden sm:inline-flex items-center gap-1.5 px-4 py-2 rounded-full text-sm font-medium text-white bg-primary hover:bg-blue-600 transition-colors shadow-sm cursor-pointer">
            免费注册
          </button>
        </template>

        <!-- 已登录 -->
        <template v-else>
          <!-- 用户下拉菜单 -->
          <div class="relative" ref="menuRef">
            <button @click="menuOpen = !menuOpen" class="flex items-center gap-2 px-3 py-2 rounded-full hover:bg-gray-50 transition-colors cursor-pointer">
              <div class="w-8 h-8 rounded-full bg-gradient-to-br from-primary to-blue-600 flex items-center justify-center text-white text-sm font-semibold">
                {{ user.email[0].toUpperCase() }}
              </div>
              <svg class="w-4 h-4 text-text-muted transition-transform" :class="{ 'rotate-180': menuOpen }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            <div v-if="menuOpen" class="absolute right-0 top-full mt-2 w-56 bg-white rounded-xl border border-border shadow-xl py-2 animate-menu-in">
              <div class="px-4 py-2 border-b border-border">
                <p class="text-sm font-medium text-text-primary truncate">{{ user.email }}</p>
                <p class="text-xs text-text-muted mt-0.5">已登录</p>
              </div>
              <button @click="menuOpen = false; $emit('logout')" class="w-full text-left px-4 py-2.5 text-sm text-text-secondary hover:bg-gray-50 transition-colors cursor-pointer flex items-center gap-2">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                </svg>
                退出登录
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </header>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'

defineProps({
  user: { type: Object, default: null },
})

defineEmits(['login', 'register', 'logout'])

const menuOpen = ref(false)
const menuRef = ref(null)

function handleClickOutside(e) {
  if (menuRef.value && !menuRef.value.contains(e.target)) {
    menuOpen.value = false
  }
}

onMounted(() => document.addEventListener('click', handleClickOutside))
onBeforeUnmount(() => document.removeEventListener('click', handleClickOutside))
</script>

<style scoped>
@keyframes menu-in {
  from { opacity: 0; transform: translateY(-4px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.animate-menu-in {
  animation: menu-in 0.15s ease-out;
}
</style>
