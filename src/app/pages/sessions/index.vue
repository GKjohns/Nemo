<script setup lang="ts">
import type { Session, Edge, Node } from '~~/app/types'

definePageMeta({ layout: 'default' })

interface SessionDetailResponse {
  session: Session
  nodes: Node[]
  edges: Edge[]
}

interface PreviewGraph {
  nodeIds: string[]
  nodeTypes: Record<string, Node['type']>
  edges: Array<{ source_id: string, target_id: string, type: Edge['type'] }>
}

const sessions = ref<Session[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const statusFilter = ref<'all' | Session['status']>('all')
const sortBy = ref<'recent' | 'status'>('recent')
const previewBySession = ref<Record<string, PreviewGraph>>({})

const statusOptions = [
  { label: 'All statuses', value: 'all' },
  { label: 'Idle', value: 'idle' },
  { label: 'Diving', value: 'diving' },
  { label: 'Reflecting', value: 'reflecting' },
  { label: 'Paused', value: 'paused' },
  { label: 'Surfaced', value: 'surfaced' }
]

const sortOptions = [
  { label: 'Most recent', value: 'recent' },
  { label: 'Status', value: 'status' }
]

function toErrorMessage(value: unknown): string {
  return value instanceof Error ? value.message : 'Unknown error'
}

function statusColor(status: Session['status']) {
  if (status === 'surfaced') return 'success'
  if (status === 'reflecting') return 'warning'
  if (status === 'diving') return 'primary'
  if (status === 'paused') return 'neutral'
  return 'info'
}

function previewPoint(index: number, total: number) {
  if (total <= 1) return { x: 72, y: 44 }
  const angle = (Math.PI * 2 * index) / total
  const radius = 28 + Math.min(total, 16)
  return {
    x: 72 + Math.cos(angle) * radius,
    y: 44 + Math.sin(angle) * radius
  }
}

function nodeColor(type: Node['type']) {
  if (type === 'hypothesis') return '#2563eb'
  if (type === 'synthesis') return '#f59e0b'
  return '#64748b'
}

const filteredSessions = computed(() => {
  const next = sessions.value.filter((session) => {
    return statusFilter.value === 'all' || session.status === statusFilter.value
  })

  if (sortBy.value === 'status') {
    return next.sort((a, b) => a.status.localeCompare(b.status) || b.updated_at.localeCompare(a.updated_at))
  }

  return next.sort((a, b) => b.updated_at.localeCompare(a.updated_at))
})

async function loadSessionPreview(sessionId: string) {
  if (previewBySession.value[sessionId]) return

  try {
    const data = await $fetch<SessionDetailResponse>(`/api/sessions/${encodeURIComponent(sessionId)}`)
    const slicedNodes = data.nodes.slice(0, 18)
    const allowed = new Set(slicedNodes.map(node => node.id))
    const slicedEdges = data.edges
      .filter(edge => allowed.has(edge.source_id) && allowed.has(edge.target_id))
      .slice(0, 22)

    previewBySession.value = {
      ...previewBySession.value,
      [sessionId]: {
        nodeIds: slicedNodes.map(node => node.id),
        nodeTypes: Object.fromEntries(slicedNodes.map(node => [node.id, node.type])),
        edges: slicedEdges.map(edge => ({
          source_id: edge.source_id,
          target_id: edge.target_id,
          type: edge.type
        }))
      }
    }
  } catch {
    // Keep cards resilient; a missing preview should not break the list page.
  }
}

function edgePath(preview: PreviewGraph, sourceId: string, targetId: string) {
  const sourceIndex = preview.nodeIds.findIndex(id => id === sourceId)
  const targetIndex = preview.nodeIds.findIndex(id => id === targetId)
  if (sourceIndex === -1 || targetIndex === -1) return ''
  const start = previewPoint(sourceIndex, preview.nodeIds.length)
  const end = previewPoint(targetIndex, preview.nodeIds.length)
  return `M ${start.x} ${start.y} L ${end.x} ${end.y}`
}

async function loadSessions() {
  loading.value = true
  error.value = null
  try {
    const data = await $fetch<{ sessions: Session[] }>('/api/sessions')
    sessions.value = data.sessions
    const top = data.sessions.slice(0, 10)
    await Promise.all(top.map(session => loadSessionPreview(session.id)))
  } catch (err) {
    error.value = `Failed to load sessions: ${toErrorMessage(err)}`
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadSessions()
})
</script>

<template>
  <UDashboardPanel id="sessions">
    <template #header>
      <UDashboardNavbar title="Sessions" description="Review prior dives and restart where needed.">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>

        <template #right>
          <UButton to="/sessions/new" icon="i-lucide-plus" label="New Session" />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="space-y-4 p-4">
        <UAlert
          v-if="error"
          color="error"
          title="Could not load sessions"
          :description="error"
        />

        <div class="flex flex-wrap items-center gap-2">
          <USelectMenu
            v-model="statusFilter"
            value-key="value"
            label-key="label"
            :items="statusOptions"
            class="w-44"
          />
          <USelectMenu
            v-model="sortBy"
            value-key="value"
            label-key="label"
            :items="sortOptions"
            class="w-40"
          />
          <UButton
            label="Refresh"
            icon="i-lucide-refresh-cw"
            variant="soft"
            :loading="loading"
            @click="loadSessions"
          />
        </div>

        <div v-if="loading && sessions.length === 0" class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <div
            v-for="idx in 6"
            :key="`skeleton-${idx}`"
            class="h-44 animate-pulse rounded-lg border border-default bg-default/30"
          />
        </div>

        <div v-else-if="filteredSessions.length === 0" class="rounded-lg border border-dashed border-default p-8 text-center">
          <p class="text-sm text-muted">No sessions yet. Start a new dive to build your exploration graph.</p>
          <UButton class="mt-3" to="/sessions/new" label="New Session" icon="i-lucide-plus" color="primary" />
        </div>

        <div v-else class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <NuxtLink
            v-for="session in filteredSessions"
            :key="session.id"
            :to="`/sessions/${session.id}`"
            class="rounded-lg border border-default bg-default/20 p-3 transition hover:bg-default/40"
          >
            <div class="mb-2 flex items-start justify-between gap-2">
              <UBadge :label="session.status" :color="statusColor(session.status)" variant="soft" />
              <p class="text-xs text-muted">{{ new Date(session.updated_at).toLocaleString() }}</p>
            </div>

            <p class="line-clamp-2 text-sm font-medium">{{ session.hypothesis }}</p>

            <div class="mt-2 overflow-hidden rounded border border-default bg-default/40">
              <svg viewBox="0 0 144 88" class="h-24 w-full">
                <template v-if="previewBySession[session.id]">
                  <path
                    v-for="(edge, edgeIdx) in previewBySession[session.id]!.edges"
                    :key="`${session.id}-edge-${edgeIdx}`"
                    :d="edgePath(previewBySession[session.id]!, edge.source_id, edge.target_id)"
                    stroke="#64748b"
                    stroke-width="1.5"
                    fill="none"
                    stroke-linecap="round"
                    opacity="0.65"
                  />
                  <circle
                    v-for="(nodeId, nodeIdx) in previewBySession[session.id]!.nodeIds"
                    :key="`${session.id}-node-${nodeId}`"
                    :cx="previewPoint(nodeIdx, previewBySession[session.id]!.nodeIds.length).x"
                    :cy="previewPoint(nodeIdx, previewBySession[session.id]!.nodeIds.length).y"
                    r="3.1"
                    :fill="nodeColor(previewBySession[session.id]!.nodeTypes[nodeId] || 'insight')"
                  />
                </template>
                <template v-else>
                  <text x="12" y="48" class="fill-muted text-[10px]">Graph preview unavailable</text>
                </template>
              </svg>
            </div>

            <div class="mt-2 flex items-center gap-2 text-xs text-muted">
              <span>{{ session.node_count }} nodes</span>
              <span>·</span>
              <span>depth {{ session.max_depth }}</span>
            </div>
          </NuxtLink>
        </div>
      </div>
    </template>
  </UDashboardPanel>
</template>
