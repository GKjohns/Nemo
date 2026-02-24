<script setup lang="ts">
import type { FeedItem } from '~~/app/types'

const props = defineProps<{
  feed: FeedItem[]
}>()

const containerRef = ref<HTMLElement | null>(null)
const lockAutoScroll = ref(false)

function toIcon(item: FeedItem): string {
  if (item.event_type === 'node:created') return 'i-lucide-sparkles'
  if (item.event_type === 'node:updated') return 'i-lucide-refresh-cw'
  if (item.event_type === 'edge:created') return 'i-lucide-git-merge'
  if (item.event_type === 'session:status') return 'i-lucide-activity'
  return 'i-lucide-triangle-alert'
}

function isHighlighted(item: FeedItem): boolean {
  const text = `${item.title} ${item.detail ?? ''}`.toLowerCase()
  return text.includes('synthesis') || text.includes('reflect')
}

function onScroll() {
  const container = containerRef.value
  if (!container) return
  const distanceFromBottom = container.scrollHeight - (container.scrollTop + container.clientHeight)
  lockAutoScroll.value = distanceFromBottom > 36
}

async function scrollToBottom(force = false) {
  const container = containerRef.value
  if (!container) return
  if (lockAutoScroll.value && !force) return
  await nextTick()
  container.scrollTop = container.scrollHeight
}

watch(() => props.feed.length, async () => {
  await scrollToBottom()
})

onMounted(async () => {
  await scrollToBottom(true)
})
</script>

<template>
  <section class="flex max-h-[560px] min-h-0 flex-col rounded-lg border border-default">
    <header class="flex items-center justify-between border-b border-default px-3 py-2">
      <h3 class="text-sm font-semibold">Activity Feed</h3>
      <UButton
        label="Jump to latest"
        icon="i-lucide-arrow-down"
        size="xs"
        variant="ghost"
        @click="scrollToBottom(true)"
      />
    </header>

    <div
      ref="containerRef"
      class="min-h-0 flex-1 space-y-2 overflow-auto p-3"
      @scroll="onScroll"
    >
      <p v-if="feed.length === 0" class="text-sm text-muted">
        Waiting for live session activity...
      </p>

      <article
        v-for="item in feed"
        :key="item.id"
        class="rounded-md border px-3 py-2"
        :class="isHighlighted(item) ? 'border-warning/50 bg-warning/10' : 'border-default bg-default/30'"
      >
        <div class="flex items-start gap-2">
          <UIcon :name="toIcon(item)" class="mt-0.5 shrink-0 text-primary" />
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium">{{ item.title }}</p>
            <p v-if="item.detail" class="mt-1 text-xs text-muted">{{ item.detail }}</p>
            <p class="mt-1 text-[11px] text-muted">
              {{ new Date(item.timestamp).toLocaleTimeString() }}
            </p>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>
