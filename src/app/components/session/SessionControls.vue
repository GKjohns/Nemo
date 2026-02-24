<script setup lang="ts">
import type { SessionStatus } from '~~/app/types'

const props = defineProps<{
  status: SessionStatus
  isConnected: boolean
  nodeCount: number
  maxDepth: number
}>()

const emit = defineEmits<{
  dive: []
  pause: []
  resume: []
  stop: []
}>()

const statusColor = computed(() => {
  if (props.status === 'surfaced') return 'success'
  if (props.status === 'reflecting') return 'warning'
  if (props.status === 'diving') return 'primary'
  return 'neutral'
})

const canPause = computed(() => props.status === 'diving' || props.status === 'reflecting')
const canResume = computed(() => props.status === 'paused')
const canDive = computed(() => props.status === 'idle' || props.status === 'surfaced')
const canStop = computed(() => props.status === 'diving' || props.status === 'reflecting' || props.status === 'paused')
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-default bg-default/50 px-3 py-2">
    <div class="flex flex-wrap items-center gap-2">
      <UBadge :label="status" :color="statusColor" />
      <UBadge :label="isConnected ? 'streaming' : 'offline'" :color="isConnected ? 'success' : 'error'" variant="soft" />
      <UBadge :label="`nodes ${nodeCount}`" color="neutral" variant="soft" />
      <UBadge :label="`depth ${maxDepth}`" color="neutral" variant="soft" />
    </div>

    <div class="flex items-center gap-2">
      <UButton
        v-if="canDive"
        label="Dive"
        icon="i-lucide-play"
        size="sm"
        color="primary"
        @click="emit('dive')"
      />
      <UButton
        v-if="canPause"
        label="Pause"
        icon="i-lucide-pause"
        size="sm"
        color="warning"
        @click="emit('pause')"
      />
      <UButton
        v-if="canResume"
        label="Resume"
        icon="i-lucide-play-circle"
        size="sm"
        color="success"
        @click="emit('resume')"
      />
      <UButton
        v-if="canStop"
        label="Stop"
        icon="i-lucide-square"
        size="sm"
        color="error"
        variant="soft"
        @click="emit('stop')"
      />
    </div>
  </div>
</template>
