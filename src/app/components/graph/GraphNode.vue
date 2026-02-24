<script setup lang="ts">
import type { Node } from '~~/app/types'

const props = defineProps<{
  node: Node
  x: number
  y: number
  selected: boolean
  exploring: boolean
}>()

const emit = defineEmits<{
  select: [nodeId: string]
}>()

const toneClass = computed(() => {
  if (props.node.type === 'hypothesis') return 'text-primary'
  if (props.node.type === 'synthesis') return 'text-warning'
  return 'text-neutral'
})

const opacity = computed(() => (props.node.status === 'dead_end' ? 0.6 : 1))

const label = computed(() => {
  const source = props.node.question ?? props.node.summary ?? props.node.id
  return source.length > 34 ? `${source.slice(0, 31)}...` : source
})
</script>

<template>
  <g
    class="cursor-pointer transition-opacity duration-200"
    :opacity="opacity"
    @click="emit('select', node.id)"
  >
    <circle
      v-if="exploring"
      :cx="x"
      :cy="y"
      r="22"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      class="animate-pulse text-primary"
    />

    <circle
      v-if="selected"
      :cx="x"
      :cy="y"
      r="19"
      fill="none"
      stroke="currentColor"
      stroke-width="2.5"
      class="text-primary transition-all duration-200"
    />

    <circle
      :cx="x"
      :cy="y"
      r="14"
      fill="currentColor"
      :class="[toneClass, 'transition-all duration-200']"
    />

    <text
      :x="x"
      :y="y + 28"
      text-anchor="middle"
      fill="currentColor"
      class="pointer-events-none select-none text-[11px] font-medium text-highlighted"
    >
      {{ label }}
    </text>
  </g>
</template>
