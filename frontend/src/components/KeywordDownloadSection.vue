<template>
  <section
    id="keyword-download"
    class="relative py-12 sm:py-16 bg-white border-t border-border-light scroll-mt-20"
    aria-labelledby="keyword-download-heading"
  >
    <!-- 兼容旧锚点：#youtube-search / #x-search / #instagram-search / #bulk-download -->
    <span id="youtube-search" class="absolute top-0 left-0 h-0 w-0 overflow-hidden" aria-hidden="true" />
    <span id="x-search" class="absolute top-0 left-0 h-0 w-0 overflow-hidden" aria-hidden="true" />
    <span id="instagram-search" class="absolute top-0 left-0 h-0 w-0 overflow-hidden" aria-hidden="true" />
    <span id="bulk-download" class="absolute top-0 left-0 h-0 w-0 overflow-hidden" aria-hidden="true" />

    <div class="max-w-3xl mx-auto px-4 sm:px-6">
      <div class="text-center mb-8">
        <h2 id="keyword-download-heading" class="text-2xl sm:text-3xl font-bold text-text-primary">
          关键词 / 批量下载
        </h2>
        <p class="mt-2 text-sm text-text-secondary">
          选择下载平台，使用对应功能
        </p>
      </div>

      <div
        class="mb-5 flex flex-wrap gap-2 p-1 rounded-2xl bg-bg-section border border-border-light"
        role="tablist"
        aria-label="选择下载平台"
      >
        <button
          v-for="item in platforms"
          :key="item.id"
          type="button"
          role="tab"
          :aria-selected="platform === item.id"
          :class="[
            'flex-1 min-w-[7.5rem] px-3 py-2.5 rounded-xl text-sm font-medium transition-colors cursor-pointer',
            platform === item.id
              ? 'bg-white text-primary shadow-sm border border-border-light'
              : 'text-text-secondary hover:text-text-primary border border-transparent',
          ]"
          @click="selectPlatform(item.id)"
        >
          {{ item.label }}
        </button>
      </div>

      <div role="tabpanel" :aria-label="currentLabel">
        <YoutubeSearchSection v-show="platform === 'youtube'" embedded />
        <XSearchSection v-show="platform === 'x'" embedded />
        <InstagramSearchSection v-show="platform === 'instagram'" embedded />
        <BulkDownloadSection v-show="platform === 'bulk'" embedded />
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import YoutubeSearchSection from './YoutubeSearchSection.vue'
import XSearchSection from './XSearchSection.vue'
import InstagramSearchSection from './InstagramSearchSection.vue'
import BulkDownloadSection from './BulkDownloadSection.vue'

const platforms = [
  { id: 'youtube', label: 'YouTube 关键词', hash: 'youtube-search' },
  { id: 'x', label: 'X 关键词', hash: 'x-search' },
  { id: 'instagram', label: 'Instagram 关键词', hash: 'instagram-search' },
  { id: 'bulk', label: '表格批量', hash: 'bulk-download' },
]

const HASH_TO_PLATFORM = {
  'keyword-download': 'youtube',
  'youtube-search': 'youtube',
  'x-search': 'x',
  'instagram-search': 'instagram',
  'bulk-download': 'bulk',
}

const platform = ref('youtube')

const currentLabel = computed(
  () => platforms.find((p) => p.id === platform.value)?.label || '下载功能',
)

function selectPlatform(id) {
  platform.value = id
  const hash = platforms.find((p) => p.id === id)?.hash || 'keyword-download'
  if (typeof window !== 'undefined') {
    history.replaceState(null, '', `#${hash}`)
  }
}

function applyHash() {
  const raw = (window.location.hash || '').replace(/^#/, '')
  const mapped = HASH_TO_PLATFORM[raw]
  if (mapped) platform.value = mapped
}

onMounted(() => {
  applyHash()
  window.addEventListener('hashchange', applyHash)
})

onBeforeUnmount(() => {
  window.removeEventListener('hashchange', applyHash)
})
</script>
