<script setup lang="ts">
import type { Node } from '~~/app/types'

const props = defineProps<{
  node: Node
  supportingNodes: Node[]
}>()

const emit = defineEmits<{
  select: [nodeId: string]
}>()

const confidenceLabel = computed(() => {
  if (typeof props.node.confidence !== 'number') return 'n/a'
  const pct = Math.round(props.node.confidence * 100)
  return `${pct}%`
})
</script>

<template>
  <section class="space-y-4">
    <div class="rounded-md border border-warning/40 bg-warning/10 p-3">
      <div class="mb-2 flex items-center justify-between gap-2">
        <h4 class="text-sm font-semibold">Synthesis Summary</h4>
        <UBadge color="warning" :label="`confidence ${confidenceLabel}`" />
      </div>
      <p class="text-sm leading-relaxed">
        {{ node.summary ?? node.answer ?? 'No synthesis narrative has been generated yet.' }}
      </p>
    </div>

    <div>
      <h5 class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
        Evidence
      </h5>
      <div v-if="supportingNodes.length > 0" class="space-y-2">
        <button
          v-for="supportedNode in supportingNodes"
          :key="supportedNode.id"
          type="button"
          class="block w-full rounded-md border border-default bg-default/40 px-3 py-2 text-left hover:bg-default/70"
          @click="emit('select', supportedNode.id)"
        >
          <p class="text-xs text-muted">{{ supportedNode.type }} · depth {{ supportedNode.depth }}</p>
          <p class="text-sm font-medium">
            {{ supportedNode.question ?? supportedNode.summary ?? supportedNode.id }}
          </p>
        </button>
      </div>
      <p v-else class="text-sm text-muted">No supporting nodes listed yet.</p>
    </div>
  </section>
</template>
