<script setup lang="ts">
import ActivityFeed from '~~/app/components/session/ActivityFeed.vue'
import ExplorationGraph from '~~/app/components/graph/ExplorationGraph.vue'
import NodeDetail from '~~/app/components/session/NodeDetail.vue'
import SessionControls from '~~/app/components/session/SessionControls.vue'
import SummaryView from '~~/app/components/session/SummaryView.vue'

definePageMeta({ layout: 'default' })

const route = useRoute()
const sessionId = computed(() => {
  const value = route.params.id
  return Array.isArray(value) ? value[0] : value
})

if (!sessionId.value) {
  throw createError({ statusCode: 400, statusMessage: 'Session id is required.' })
}

const {
  session,
  nodes,
  edges,
  status,
  feed,
  isConnected,
  isHydrating,
  error,
  exploringNode,
  hypothesis,
  nodeCount,
  maxDepth,
  dive,
  pause,
  resume,
  stop
} = useSession(sessionId.value)

const sortedNodes = computed(() =>
  Array.from(nodes.value.values()).sort((a, b) => a.created_at.localeCompare(b.created_at))
)

const selectedNodeId = ref<string | null>(null)

const selectedNode = computed(() => {
  if (!selectedNodeId.value) return null
  return nodes.value.get(selectedNodeId.value) ?? null
})

const viewMode = ref<'live' | 'summary'>('live')

const hypothesisText = computed(() => {
  return session.value?.hypothesis ?? hypothesis.value?.question ?? hypothesis.value?.summary ?? 'Live exploration stream'
})

watch(sortedNodes, (nextNodes) => {
  if (nextNodes.length === 0) {
    selectedNodeId.value = null
    return
  }

  if (selectedNodeId.value && nodes.value.has(selectedNodeId.value)) return
  selectedNodeId.value = nextNodes[0]?.id ?? null
}, { immediate: true })

watch(status, (nextStatus) => {
  if (nextStatus === 'surfaced') {
    viewMode.value = 'summary'
  }
})
</script>

<template>
  <UDashboardPanel id="session-detail">
    <template #header>
      <UDashboardNavbar :title="`Session ${sessionId}`" :description="hypothesisText">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <div class="flex items-center gap-2 text-xs text-muted">
            <UIcon name="i-lucide-git-branch-plus" />
            <span>{{ edges.length }} edges</span>
          </div>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="space-y-4 p-4">
        <UAlert
          v-if="error"
          color="error"
          title="Session stream error"
          :description="error"
        />

        <SessionControls
          :status="status"
          :is-connected="isConnected"
          :node-count="nodeCount"
          :max-depth="maxDepth"
          @dive="dive"
          @pause="pause"
          @resume="resume"
          @stop="stop"
        />

        <div class="flex items-center gap-2">
          <UButton
            label="Live View"
            icon="i-lucide-workflow"
            size="sm"
            variant="soft"
            :color="viewMode === 'live' ? 'primary' : 'neutral'"
            @click="viewMode = 'live'"
          />
          <UButton
            label="Summary View"
            icon="i-lucide-clipboard-list"
            size="sm"
            variant="soft"
            :color="viewMode === 'summary' ? 'primary' : 'neutral'"
            @click="viewMode = 'summary'"
          />
        </div>

        <div v-if="isHydrating" class="rounded-lg border border-default p-4">
          <p class="text-sm text-muted">
            Hydrating graph snapshot and reconnecting stream...
          </p>
        </div>

        <SummaryView
          v-if="viewMode === 'summary'"
          :session="session"
          :nodes="sortedNodes"
          @select="selectedNodeId = $event"
        />

        <div v-else class="grid min-h-[560px] grid-cols-1 gap-4 xl:grid-cols-12">
          <div class="xl:col-span-6">
            <ExplorationGraph
              :nodes="sortedNodes"
              :edges="edges"
              :selected-node-id="selectedNodeId"
              :exploring-node-id="exploringNode?.id ?? null"
              @select="selectedNodeId = $event"
            />
          </div>

          <div class="xl:col-span-3">
            <ActivityFeed :feed="feed" />
          </div>

          <div class="xl:col-span-3">
            <NodeDetail
              :node="selectedNode"
              :all-nodes="sortedNodes"
              :edges="edges"
              @select="selectedNodeId = $event"
            />
          </div>
        </div>
      </div>
    </template>
  </UDashboardPanel>
</template>
