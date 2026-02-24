<script setup lang="ts">
import type { EdgeType } from '~~/app/types'

const props = defineProps<{
  type: EdgeType
  x1: number
  y1: number
  x2: number
  y2: number
}>()

const toneClass = computed(() => {
  if (props.type === 'supports') return 'text-success'
  if (props.type === 'conflicts') return 'text-error'
  if (props.type === 'refines') return 'text-warning'
  return 'text-info'
})
</script>

<template>
  <line
    :x1="x1"
    :y1="y1"
    :x2="x2"
    :y2="y2"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    marker-end="url(#edge-arrow)"
    :class="[toneClass, 'transition-all duration-300']"
  />
</template>
