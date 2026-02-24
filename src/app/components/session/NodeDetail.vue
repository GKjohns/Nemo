<script setup lang="ts">
import type { TabsItem } from '@nuxt/ui'
import type { Edge, Node } from '~~/app/types'
import NodeResult from '~~/app/components/session/NodeResult.vue'
import SynthesisSummary from '~~/app/components/session/SynthesisSummary.vue'

const props = defineProps<{
  node: Node | null
  allNodes: Node[]
  edges: Edge[]
}>()

const emit = defineEmits<{
  select: [nodeId: string]
}>()

const tabs = [
  { label: 'Question', icon: 'i-lucide-circle-help', slot: 'question', value: 'question' },
  { label: 'Code', icon: 'i-lucide-file-code', slot: 'code', value: 'code' },
  { label: 'Result', icon: 'i-lucide-table-properties', slot: 'result', value: 'result' },
  { label: 'Interpretation', icon: 'i-lucide-message-square-text', slot: 'interpretation', value: 'interpretation' },
  { label: 'Connections', icon: 'i-lucide-network', slot: 'connections', value: 'connections' }
] satisfies TabsItem[]

const activeTab = ref('question')

interface ConnectionView {
  id: string
  type: Edge['type']
  direction: 'in' | 'out'
  reasoning: string | null
  other: Node | null
}

const nodeLookup = computed(() => {
  return new Map(props.allNodes.map(node => [node.id, node]))
})

const supportingNodes = computed(() => {
  const currentNode = props.node
  if (!currentNode?.supported_by?.length) return []
  return currentNode.supported_by
    .map(id => nodeLookup.value.get(id) ?? null)
    .filter((node): node is Node => node !== null)
})

const connections = computed<ConnectionView[]>(() => {
  const currentNode = props.node
  if (!currentNode) return []

  return props.edges
    .filter(edge => edge.source_id === currentNode.id || edge.target_id === currentNode.id)
    .map((edge) => {
      const direction: 'in' | 'out' = edge.source_id === currentNode.id ? 'out' : 'in'
      const otherId = direction === 'out' ? edge.target_id : edge.source_id
      return {
        id: edge.id,
        type: edge.type,
        direction,
        reasoning: edge.reasoning,
        other: nodeLookup.value.get(otherId) ?? null
      }
    })
})

watch(() => props.node?.id, () => {
  activeTab.value = 'question'
})
</script>

<template>
  <section class="flex max-h-[560px] min-h-0 flex-col rounded-lg border border-default">
    <header class="border-b border-default px-3 py-2">
      <h3 class="text-sm font-semibold">Node Detail</h3>
      <p v-if="node" class="mt-1 text-xs text-muted">
        {{ node.type }} · {{ node.status }} · depth {{ node.depth }}
      </p>
    </header>

    <div class="min-h-0 flex-1 overflow-auto p-3">
      <p v-if="!node" class="text-sm text-muted">
        Select a node in the graph to inspect its question, execution, and connections.
      </p>

      <template v-else-if="node.type === 'synthesis'">
        <SynthesisSummary
          :node="node"
          :supporting-nodes="supportingNodes"
          @select="emit('select', $event)"
        />
      </template>

      <template v-else>
        <UTabs
          v-model="activeTab"
          :items="tabs"
          color="neutral"
          variant="link"
          :ui="{ trigger: 'grow' }"
          class="w-full gap-3"
        >
          <template #question>
            <div class="space-y-2 rounded-md border border-default bg-default/40 p-3">
              <p class="text-xs uppercase tracking-wide text-muted">Question</p>
              <p class="text-sm">{{ node.question ?? 'No question text available.' }}</p>
            </div>
          </template>

          <template #code>
            <div class="space-y-2">
              <p class="text-xs uppercase tracking-wide text-muted">Generated SQL</p>
              <pre class="overflow-auto rounded-md border border-default bg-default/40 p-3 text-xs">{{ node.code ?? '-- No SQL generated yet --' }}</pre>
            </div>
          </template>

          <template #result>
            <NodeResult :node="node" />
          </template>

          <template #interpretation>
            <div class="space-y-3 rounded-md border border-default bg-default/40 p-3">
              <div class="flex items-center justify-between gap-2">
                <p class="text-xs uppercase tracking-wide text-muted">Interpretation</p>
                <UBadge
                  color="primary"
                  variant="soft"
                  :label="`confidence ${typeof node.confidence === 'number' ? `${Math.round(node.confidence * 100)}%` : 'n/a'}`"
                />
              </div>
              <p class="text-sm leading-relaxed">
                {{ node.answer ?? 'No interpretation generated yet.' }}
              </p>
            </div>
          </template>

          <template #connections>
            <div class="space-y-2">
              <p v-if="connections.length === 0" class="text-sm text-muted">
                No graph connections available yet.
              </p>
              <article
                v-for="connection in connections"
                :key="connection.id"
                class="rounded-md border border-default bg-default/40 p-3"
              >
                <div class="mb-1 flex items-center gap-2">
                  <UBadge
                    size="xs"
                    variant="soft"
                    :color="connection.direction === 'out' ? 'primary' : 'neutral'"
                    :label="connection.direction === 'out' ? 'outgoing' : 'incoming'"
                  />
                  <UBadge size="xs" color="neutral" variant="soft" :label="connection.type" />
                </div>

                <button
                  v-if="connection.other"
                  type="button"
                  class="text-left text-sm font-medium text-primary hover:underline"
                  @click="emit('select', connection.other.id)"
                >
                  {{ connection.other.question ?? connection.other.summary ?? connection.other.id }}
                </button>
                <p v-else class="text-sm text-muted">Linked node not found</p>

                <p v-if="connection.reasoning" class="mt-1 text-xs text-muted">
                  {{ connection.reasoning }}
                </p>
              </article>
            </div>
          </template>
        </UTabs>
      </template>
    </div>
  </section>
</template>
