<script setup lang="ts">
import type { TabsItem } from '@nuxt/ui'

definePageMeta({ layout: 'default' })

interface HealthCheck {
  name: string
  status: 'ok' | 'error'
  latency_ms?: number
  detail?: string
}

interface HealthResult {
  status: string
  checks: HealthCheck[]
}

interface ExecutorResult {
  result: { type: string, data: unknown }
  latency_ms: number
  sql: string
  csv_path: string
}

interface FrontierCase {
  label: string
  parentConfidence: number | null
  depth: number
  deadEndSiblings: number
  priority: number
}

interface GraphResult {
  log: string[]
  context: {
    hypothesis: string
    nodes: unknown[]
    edges: unknown[]
    stats: { node_count: number, edge_count: number, max_depth: number, frontier_count: number }
  }
  latency_ms: number
}

interface DatasetLite {
  id: string
  name: string
}

interface SessionLite {
  id: string
  dataset_id: string
  hypothesis: string
  context: string | null
  status: string
  config: unknown
  node_count: number
  max_depth: number
  created_at: string
  updated_at: string
}

interface SessionsListResult {
  sessions: SessionLite[]
}

interface SessionDetailResult {
  session: SessionLite
  nodes: unknown[]
  edges: unknown[]
}

interface StreamingTestResult {
  ok: boolean
  session_id: string
  timeout_ms: number
  start_error: string | null
  history_before: number
  history_after: number
  new_persisted_events: number
  live_events_received: number
  live_event_types: string[]
}

const tabs = [
  { label: 'Health', icon: 'i-lucide-heart-pulse', slot: 'health', value: 'health' },
  { label: 'Executor', icon: 'i-lucide-database', slot: 'executor', value: 'executor' },
  { label: 'Frontier', icon: 'i-lucide-target', slot: 'frontier', value: 'frontier' },
  { label: 'Graph', icon: 'i-lucide-git-fork', slot: 'graph', value: 'graph' },
  { label: 'Sessions API', icon: 'i-lucide-activity', slot: 'sessions', value: 'sessions' },
  { label: 'Streaming', icon: 'i-lucide-radio', slot: 'streaming', value: 'streaming' }
] satisfies TabsItem[]

const healthData = ref<HealthResult | null>(null)
const healthLoading = ref(false)
const healthError = ref<string | null>(null)

const executorResult = ref<ExecutorResult | null>(null)
const executorLoading = ref(false)
const executorError = ref<string | null>(null)
const executorSql = ref('SELECT\n  ROUND(AVG(final_grade), 2) AS avg_grade,\n  ROUND(AVG(sleep_hours), 2) AS avg_sleep,\n  COUNT(*) AS n\nFROM dataset\nGROUP BY CASE\n  WHEN sleep_hours < 4 THEN \'<4h\'\n  WHEN sleep_hours < 6 THEN \'4-6h\'\n  WHEN sleep_hours < 8 THEN \'6-8h\'\n  ELSE \'8h+\'\nEND\nORDER BY avg_sleep')

const frontierResults = ref<FrontierCase[] | null>(null)
const frontierLoading = ref(false)

const graphResult = ref<GraphResult | null>(null)
const graphLoading = ref(false)
const graphError = ref<string | null>(null)

const datasets = ref<DatasetLite[]>([])
const datasetsLoading = ref(false)
const datasetsError = ref<string | null>(null)
const selectedDatasetId = ref('')

const sessionHypothesis = ref('Students who sleep more have higher final grades.')
const sessionContext = ref('Dev harness session for API and streaming smoke tests.')
const createdSession = ref<SessionLite | null>(null)
const sessionsList = ref<SessionLite[]>([])
const selectedSessionId = ref('')
const sessionDetail = ref<SessionDetailResult | null>(null)
const sessionsLoading = ref(false)
const sessionsError = ref<string | null>(null)
const sessionActionMessage = ref<string | null>(null)

const streamingTimeoutMs = ref(8000)
const streamingLoading = ref(false)
const streamingError = ref<string | null>(null)
const streamingResult = ref<StreamingTestResult | null>(null)

const activeTab = ref('health')

async function runHealthCheck() {
  healthLoading.value = true
  healthError.value = null
  try {
    healthData.value = await $fetch<HealthResult>('/api/dev/health')
  } catch (err) {
    healthError.value = err instanceof Error ? err.message : 'Health check failed'
  } finally {
    healthLoading.value = false
  }
}

async function runExecutorTest() {
  executorLoading.value = true
  executorError.value = null
  try {
    executorResult.value = await $fetch<ExecutorResult>('/api/dev/test-executor', {
      method: 'POST',
      body: { sql: executorSql.value }
    })
  } catch (err) {
    executorError.value = err instanceof Error ? err.message : 'Executor test failed'
  } finally {
    executorLoading.value = false
  }
}

async function runFrontierTest() {
  frontierLoading.value = true
  try {
    const data = await $fetch<{ results: FrontierCase[] }>('/api/dev/test-frontier')
    frontierResults.value = data.results
  } finally {
    frontierLoading.value = false
  }
}

async function runGraphTest() {
  graphLoading.value = true
  graphError.value = null
  try {
    graphResult.value = await $fetch<GraphResult>('/api/dev/test-graph', { method: 'POST' })
  } catch (err) {
    graphError.value = err instanceof Error ? err.message : 'Graph test failed'
  } finally {
    graphLoading.value = false
  }
}

async function loadDatasets() {
  datasetsLoading.value = true
  datasetsError.value = null
  try {
    const data = await $fetch<{ datasets: DatasetLite[] }>('/api/datasets')
    datasets.value = data.datasets
    const firstDataset = data.datasets[0]
    if (!selectedDatasetId.value && firstDataset) {
      selectedDatasetId.value = firstDataset.id
    }
  } catch (err) {
    datasetsError.value = err instanceof Error ? err.message : 'Failed to load datasets'
  } finally {
    datasetsLoading.value = false
  }
}

async function listSessions() {
  sessionsLoading.value = true
  sessionsError.value = null
  sessionActionMessage.value = null
  try {
    const data = await $fetch<SessionsListResult>('/api/sessions')
    sessionsList.value = data.sessions
    const firstSession = data.sessions[0]
    if (!selectedSessionId.value && firstSession) {
      selectedSessionId.value = firstSession.id
    }
  } catch (err) {
    sessionsError.value = err instanceof Error ? err.message : 'Failed to list sessions'
  } finally {
    sessionsLoading.value = false
  }
}

async function createSession() {
  if (!selectedDatasetId.value) {
    sessionsError.value = 'Pick a dataset before creating a session.'
    return
  }

  sessionsLoading.value = true
  sessionsError.value = null
  sessionActionMessage.value = null
  try {
    const response = await $fetch<{ session: SessionLite }>('/api/sessions', {
      method: 'POST',
      body: {
        dataset_id: selectedDatasetId.value,
        hypothesis: sessionHypothesis.value,
        context: sessionContext.value
      }
    })
    createdSession.value = response.session
    selectedSessionId.value = response.session.id
    sessionActionMessage.value = `Created session ${response.session.id}`
    await listSessions()
    await loadSessionDetail()
  } catch (err) {
    sessionsError.value = err instanceof Error ? err.message : 'Failed to create session'
  } finally {
    sessionsLoading.value = false
  }
}

async function loadSessionDetail() {
  if (!selectedSessionId.value) return
  sessionsLoading.value = true
  sessionsError.value = null
  sessionActionMessage.value = null
  try {
    sessionDetail.value = await $fetch<SessionDetailResult>(`/api/sessions/${encodeURIComponent(selectedSessionId.value)}`)
  } catch (err) {
    sessionsError.value = err instanceof Error ? err.message : 'Failed to load session detail'
  } finally {
    sessionsLoading.value = false
  }
}

async function runSessionAction(action: 'dive' | 'pause' | 'resume') {
  if (!selectedSessionId.value) {
    sessionsError.value = 'Choose a session first.'
    return
  }

  sessionsLoading.value = true
  sessionsError.value = null
  sessionActionMessage.value = null
  try {
    await $fetch(`/api/sessions/${encodeURIComponent(selectedSessionId.value)}/${action}`, { method: 'POST' })
    sessionActionMessage.value = `${action} request accepted for ${selectedSessionId.value}`
    await loadSessionDetail()
  } catch (err) {
    sessionsError.value = err instanceof Error ? err.message : `Failed to ${action} session`
  } finally {
    sessionsLoading.value = false
  }
}

async function runStreamingTest() {
  if (!selectedSessionId.value) {
    streamingError.value = 'Choose or create a session before running the stream test.'
    return
  }

  streamingLoading.value = true
  streamingError.value = null
  try {
    streamingResult.value = await $fetch<StreamingTestResult>('/api/dev/test-streaming', {
      method: 'POST',
      body: {
        session_id: selectedSessionId.value,
        timeout_ms: streamingTimeoutMs.value
      }
    })
  } catch (err) {
    streamingError.value = err instanceof Error ? err.message : 'Streaming test failed'
  } finally {
    streamingLoading.value = false
  }
}

onMounted(() => {
  runHealthCheck()
  loadDatasets()
  listSessions()
})
</script>

<template>
  <UDashboardPanel id="dev-tools">
    <template #header>
      <UDashboardNavbar title="Dev Tools" description="Sprint 2 test harness — health checks, executor, frontier scoring, graph store">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="max-w-6xl p-4">
        <UTabs
          v-model="activeTab"
          default-value="health"
          :items="tabs"
          color="neutral"
          variant="link"
          :ui="{ trigger: 'grow' }"
          class="w-full gap-4"
        >
          <template #health>
            <section>
              <div class="flex items-center justify-between mb-3">
                <h2 class="text-lg font-semibold flex items-center gap-2">
                  <UIcon name="i-lucide-heart-pulse" class="text-primary" />
                  System Health
                </h2>
                <UButton
                  label="Run Check"
                  icon="i-lucide-refresh-cw"
                  size="sm"
                  :loading="healthLoading"
                  @click="runHealthCheck"
                />
              </div>

              <UAlert
                v-if="healthError"
                color="error"
                title="Health check failed"
                :description="healthError"
                class="mb-3"
              />

              <div v-if="healthData" class="space-y-2">
                <UBadge
                  :color="healthData.status === 'healthy' ? 'success' : 'warning'"
                  :label="healthData.status === 'healthy' ? 'All Systems Healthy' : 'Degraded'"
                  size="lg"
                  class="mb-2"
                />

                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <div
                    v-for="check in healthData.checks"
                    :key="check.name"
                    class="flex items-center gap-2 rounded-md border border-default px-3 py-2 text-sm"
                  >
                    <UIcon
                      :name="check.status === 'ok' ? 'i-lucide-check-circle' : 'i-lucide-x-circle'"
                      :class="check.status === 'ok' ? 'text-success' : 'text-error'"
                    />
                    <span class="font-mono text-xs">{{ check.name }}</span>
                    <span class="ml-auto text-muted text-xs truncate max-w-48">{{ check.detail }}</span>
                  </div>
                </div>
              </div>
            </section>
          </template>

          <template #executor>
            <section>
              <div class="flex items-center justify-between mb-3">
                <h2 class="text-lg font-semibold flex items-center gap-2">
                  <UIcon name="i-lucide-database" class="text-primary" />
                  DuckDB Executor
                </h2>
                <UButton
                  label="Run Query"
                  icon="i-lucide-play"
                  size="sm"
                  :loading="executorLoading"
                  @click="runExecutorTest"
                />
              </div>

              <p class="text-xs text-muted mb-2">
                Runs SQL via DuckDB against the dummy student productivity CSV (20k rows). Table alias is <code>dataset</code>.
              </p>

              <UTextarea
                v-model="executorSql"
                :rows="6"
                :cols="50"
                class="w-full font-mono text-sm mb-3"
                placeholder="SELECT * FROM dataset LIMIT 10"
              />

              <UAlert
                v-if="executorError"
                color="error"
                title="Executor error"
                :description="executorError"
                class="mb-3"
              />

              <div v-if="executorResult" class="space-y-2">
                <div class="flex items-center gap-3 text-sm">
                  <UBadge :color="executorResult.result.type === 'error' ? 'error' : 'success'" :label="executorResult.result.type" />
                  <span class="text-muted">{{ executorResult.latency_ms }}ms</span>
                </div>

                <div
                  v-if="executorResult.result.type === 'table' && executorResult.result.data"
                  class="overflow-x-auto border border-default rounded-md"
                >
                  <table class="min-w-full text-xs">
                    <thead class="bg-elevated">
                      <tr>
                        <th
                          v-for="col in (executorResult.result.data as any).columns"
                          :key="col"
                          class="px-3 py-2 text-left font-medium"
                        >
                          {{ col }}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(row, i) in (executorResult.result.data as any).rows"
                        :key="i"
                        class="border-t border-default"
                      >
                        <td v-for="(cell, j) in row" :key="j" class="px-3 py-1.5 font-mono">
                          {{ cell }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <div class="px-3 py-2 text-xs text-muted bg-elevated border-t border-default">
                    {{ (executorResult.result.data as any).row_count }} rows
                    <span v-if="(executorResult.result.data as any).truncated"> (truncated)</span>
                  </div>
                </div>

                <div v-else-if="executorResult.result.type === 'scalar'" class="p-4 border border-default rounded-md text-center">
                  <div class="text-3xl font-bold">
                    {{ executorResult.result.data }}
                  </div>
                </div>

                <UAlert
                  v-else-if="executorResult.result.type === 'error'"
                  color="error"
                  :title="(executorResult.result.data as any)?.message"
                  :description="(executorResult.result.data as any)?.detail"
                />
              </div>
            </section>
          </template>

          <template #frontier>
            <section>
              <div class="flex items-center justify-between mb-3">
                <h2 class="text-lg font-semibold flex items-center gap-2">
                  <UIcon name="i-lucide-target" class="text-primary" />
                  Frontier Priority Scoring
                </h2>
                <UButton
                  label="Compute"
                  icon="i-lucide-calculator"
                  size="sm"
                  :loading="frontierLoading"
                  @click="runFrontierTest"
                />
              </div>

              <p class="text-xs text-muted mb-2">
                Shows how the priority formula ranks different node scenarios. Higher = explored sooner.
              </p>

              <div v-if="frontierResults" class="overflow-x-auto border border-default rounded-md">
                <table class="min-w-full text-xs">
                  <thead class="bg-elevated">
                    <tr>
                      <th class="px-3 py-2 text-left font-medium">
                        Scenario
                      </th>
                      <th class="px-3 py-2 text-right font-medium">
                        Parent Conf.
                      </th>
                      <th class="px-3 py-2 text-right font-medium">
                        Depth
                      </th>
                      <th class="px-3 py-2 text-right font-medium">
                        Dead Siblings
                      </th>
                      <th class="px-3 py-2 text-right font-medium">
                        Priority
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="(c, i) in frontierResults"
                      :key="i"
                      class="border-t border-default"
                    >
                      <td class="px-3 py-1.5">
                        {{ c.label }}
                      </td>
                      <td class="px-3 py-1.5 text-right font-mono">
                        {{ c.parentConfidence ?? 'null' }}
                      </td>
                      <td class="px-3 py-1.5 text-right font-mono">
                        {{ c.depth }}
                      </td>
                      <td class="px-3 py-1.5 text-right font-mono">
                        {{ c.deadEndSiblings }}
                      </td>
                      <td class="px-3 py-1.5 text-right font-mono font-bold">
                        {{ c.priority }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </template>

          <template #graph>
            <section>
              <div class="flex items-center justify-between mb-3">
                <h2 class="text-lg font-semibold flex items-center gap-2">
                  <UIcon name="i-lucide-git-fork" class="text-primary" />
                  Graph Store (In-Memory)
                </h2>
                <UButton
                  label="Run Test"
                  icon="i-lucide-play"
                  size="sm"
                  :loading="graphLoading"
                  @click="runGraphTest"
                />
              </div>

              <p class="text-xs text-muted mb-2">
                Creates an in-memory graph (hypothesis + 2 insights + edge), tests frontier selection and context generation.
              </p>

              <UAlert
                v-if="graphError"
                color="error"
                title="Graph test failed"
                :description="graphError"
                class="mb-3"
              />

              <div v-if="graphResult" class="space-y-3">
                <div class="flex items-center gap-3 text-sm">
                  <UBadge color="success" label="Passed" />
                  <span class="text-muted">{{ graphResult.latency_ms }}ms</span>
                </div>

                <div class="border border-default rounded-md p-3 space-y-1">
                  <div class="text-xs font-semibold mb-1">
                    Operation Log
                  </div>
                  <div v-for="(line, i) in graphResult.log" :key="i" class="text-xs font-mono text-muted">
                    {{ line }}
                  </div>
                </div>

                <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <div class="border border-default rounded-md p-3 text-center">
                    <div class="text-2xl font-bold">
                      {{ graphResult.context.stats.node_count }}
                    </div>
                    <div class="text-xs text-muted">
                      Nodes
                    </div>
                  </div>
                  <div class="border border-default rounded-md p-3 text-center">
                    <div class="text-2xl font-bold">
                      {{ graphResult.context.stats.edge_count }}
                    </div>
                    <div class="text-xs text-muted">
                      Edges
                    </div>
                  </div>
                  <div class="border border-default rounded-md p-3 text-center">
                    <div class="text-2xl font-bold">
                      {{ graphResult.context.stats.max_depth }}
                    </div>
                    <div class="text-xs text-muted">
                      Max Depth
                    </div>
                  </div>
                  <div class="border border-default rounded-md p-3 text-center">
                    <div class="text-2xl font-bold">
                      {{ graphResult.context.stats.frontier_count }}
                    </div>
                    <div class="text-xs text-muted">
                      Frontier
                    </div>
                  </div>
                </div>
              </div>
            </section>
          </template>

          <template #sessions>
            <section class="space-y-4">
              <div class="flex items-center justify-between">
                <h2 class="text-lg font-semibold flex items-center gap-2">
                  <UIcon name="i-lucide-activity" class="text-primary" />
                  Sessions API Flow
                </h2>
                <div class="flex items-center gap-2">
                  <UButton
                    label="Reload datasets"
                    icon="i-lucide-database"
                    size="sm"
                    variant="soft"
                    :loading="datasetsLoading"
                    @click="loadDatasets"
                  />
                  <UButton
                    label="Reload sessions"
                    icon="i-lucide-refresh-cw"
                    size="sm"
                    variant="soft"
                    :loading="sessionsLoading"
                    @click="listSessions"
                  />
                </div>
              </div>

              <UAlert
                v-if="datasetsError"
                color="error"
                title="Dataset load failed"
                :description="datasetsError"
              />
              <UAlert
                v-if="sessionsError"
                color="error"
                title="Session API error"
                :description="sessionsError"
              />
              <UAlert
                v-if="sessionActionMessage"
                color="success"
                title="Session action"
                :description="sessionActionMessage"
              />

              <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <div class="border border-default rounded-md p-4 space-y-3">
                  <h3 class="font-medium">
                    Create session
                  </h3>

                  <UFormField label="Dataset">
                    <USelectMenu
                      v-model="selectedDatasetId"
                      value-key="id"
                      label-key="name"
                      :items="datasets"
                      class="w-full"
                      placeholder="Select a dataset"
                    />
                  </UFormField>

                  <UFormField label="Hypothesis">
                    <UTextarea
                      v-model="sessionHypothesis"
                      :rows="3"
                      class="w-full"
                      placeholder="Hypothesis text"
                    />
                  </UFormField>

                  <UFormField label="Context">
                    <UTextarea
                      v-model="sessionContext"
                      :rows="2"
                      class="w-full"
                      placeholder="Optional context"
                    />
                  </UFormField>

                  <UButton
                    label="Create session"
                    icon="i-lucide-plus"
                    color="primary"
                    :loading="sessionsLoading"
                    @click="createSession"
                  />

                  <div v-if="createdSession" class="text-xs text-muted">
                    Last created: <code>{{ createdSession.id }}</code>
                  </div>
                </div>

                <div class="border border-default rounded-md p-4 space-y-3">
                  <h3 class="font-medium">
                    Session actions
                  </h3>

                  <UFormField label="Session">
                    <USelectMenu
                      v-model="selectedSessionId"
                      value-key="id"
                      label-key="id"
                      :items="sessionsList"
                      class="w-full"
                      placeholder="Select a session"
                    />
                  </UFormField>

                  <div class="flex flex-wrap gap-2">
                    <UButton label="Detail" icon="i-lucide-file-search" variant="soft" :loading="sessionsLoading" @click="loadSessionDetail" />
                    <UButton label="Dive" icon="i-lucide-play" color="primary" :loading="sessionsLoading" @click="runSessionAction('dive')" />
                    <UButton label="Pause" icon="i-lucide-pause" color="warning" :loading="sessionsLoading" @click="runSessionAction('pause')" />
                    <UButton label="Resume" icon="i-lucide-play-circle" color="success" :loading="sessionsLoading" @click="runSessionAction('resume')" />
                  </div>

                  <div v-if="sessionDetail" class="grid grid-cols-2 gap-2 text-xs">
                    <div class="border border-default rounded px-2 py-1">
                      Status: <span class="font-mono">{{ sessionDetail.session.status }}</span>
                    </div>
                    <div class="border border-default rounded px-2 py-1">
                      Nodes: <span class="font-mono">{{ sessionDetail.nodes.length }}</span>
                    </div>
                    <div class="border border-default rounded px-2 py-1">
                      Edges: <span class="font-mono">{{ sessionDetail.edges.length }}</span>
                    </div>
                    <div class="border border-default rounded px-2 py-1">
                      Max depth: <span class="font-mono">{{ sessionDetail.session.max_depth }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </section>
          </template>

          <template #streaming>
            <section class="space-y-4">
              <div class="flex items-center justify-between">
                <h2 class="text-lg font-semibold flex items-center gap-2">
                  <UIcon name="i-lucide-radio" class="text-primary" />
                  Streaming Smoke Test
                </h2>
                <UButton
                  label="Run stream test"
                  icon="i-lucide-play"
                  color="primary"
                  :loading="streamingLoading"
                  @click="runStreamingTest"
                />
              </div>

              <p class="text-xs text-muted">
                Uses <code>/api/dev/test-streaming</code> to subscribe, kick off session execution, wait for events, then pause.
              </p>

              <UAlert
                v-if="streamingError"
                color="error"
                title="Streaming test failed"
                :description="streamingError"
              />

              <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                <UFormField label="Session id">
                  <UInput v-model="selectedSessionId" class="w-full" placeholder="Session id to stream-test" />
                </UFormField>
                <UFormField label="Timeout (ms)">
                  <UInput v-model.number="streamingTimeoutMs" class="w-full" type="number" min="1000" max="30000" />
                </UFormField>
              </div>

              <div v-if="streamingResult" class="space-y-2">
                <div class="flex items-center gap-2">
                  <UBadge :color="streamingResult.ok ? 'success' : 'warning'" :label="streamingResult.ok ? 'Live events received' : 'No events received'" />
                  <span class="text-xs text-muted">Session: <code>{{ streamingResult.session_id }}</code></span>
                </div>

                <div class="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
                  <div class="border border-default rounded px-2 py-1">History before: <span class="font-mono">{{ streamingResult.history_before }}</span></div>
                  <div class="border border-default rounded px-2 py-1">History after: <span class="font-mono">{{ streamingResult.history_after }}</span></div>
                  <div class="border border-default rounded px-2 py-1">New persisted: <span class="font-mono">{{ streamingResult.new_persisted_events }}</span></div>
                  <div class="border border-default rounded px-2 py-1">Live events: <span class="font-mono">{{ streamingResult.live_events_received }}</span></div>
                  <div class="border border-default rounded px-2 py-1">Timeout: <span class="font-mono">{{ streamingResult.timeout_ms }}ms</span></div>
                  <div class="border border-default rounded px-2 py-1">Start error: <span class="font-mono">{{ streamingResult.start_error ?? 'none' }}</span></div>
                </div>

                <div class="border border-default rounded p-2">
                  <div class="text-xs font-semibold mb-1">
                    Event types
                  </div>
                  <div class="flex flex-wrap gap-2">
                    <UBadge
                      v-for="(eventType, i) in streamingResult.live_event_types"
                      :key="`${eventType}-${i}`"
                      color="neutral"
                      variant="soft"
                      :label="eventType"
                    />
                  </div>
                </div>
              </div>
            </section>
          </template>
        </UTabs>
      </div>
    </template>
  </UDashboardPanel>
</template>
