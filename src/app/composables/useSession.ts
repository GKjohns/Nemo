import type { Edge, FeedItem, NemoEvent, Node, Session, SessionStatus } from '~~/app/types'

interface SessionGraphResponse {
  session: Session
  nodes: Node[]
  edges: Edge[]
}

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error'
}

function isAlreadyRunningError(error: unknown): boolean {
  const message = toErrorMessage(error).toLowerCase()
  return message.includes('already running')
}

function createFeedItem(event: NemoEvent): FeedItem {
  const timestamp = new Date().toISOString()
  const id = `${timestamp}-${Math.random().toString(36).slice(2, 10)}`

  if (event.type === 'node:created') {
    return {
      id,
      timestamp,
      event_type: event.type,
      title: `Node created: ${event.node.type}`,
      detail: event.node.question ?? event.node.summary ?? null,
      node_id: event.node.id
    }
  }

  if (event.type === 'node:updated') {
    return {
      id,
      timestamp,
      event_type: event.type,
      title: `Node updated: ${event.node.status}`,
      detail: event.node.question ?? event.node.answer ?? null,
      node_id: event.node.id
    }
  }

  if (event.type === 'edge:created') {
    return {
      id,
      timestamp,
      event_type: event.type,
      title: `Edge created: ${event.edge.type}`,
      detail: event.edge.reasoning,
      node_id: event.edge.target_id
    }
  }

  if (event.type === 'session:status') {
    return {
      id,
      timestamp,
      event_type: event.type,
      title: `Session ${event.status}`,
      detail: null,
      node_id: null
    }
  }

  return {
    id,
    timestamp,
    event_type: event.type,
    title: 'Session error',
    detail: event.error,
    node_id: null
  }
}

function isNemoEvent(value: unknown): value is NemoEvent {
  return Boolean(value && typeof value === 'object' && 'type' in value)
}

export function useSession(sessionId: string) {
  const session = ref<Session | null>(null)
  const nodes = ref<Map<string, Node>>(new Map())
  const edges = ref<Edge[]>([])
  const status = ref<SessionStatus>('idle')
  const feed = ref<FeedItem[]>([])
  const isConnected = ref(false)
  const isHydrating = ref(false)
  const error = ref<string | null>(null)
  let source: EventSource | null = null

  const exploringNode = computed(() => {
    for (const node of nodes.value.values()) {
      if (node.status === 'exploring') return node
    }
    return null
  })

  const frontier = computed(() =>
    Array.from(nodes.value.values()).filter(node => node.status === 'frontier')
  )

  const syntheses = computed(() =>
    Array.from(nodes.value.values()).filter(node => node.type === 'synthesis')
  )

  const hypothesis = computed(() =>
    Array.from(nodes.value.values()).find(node => node.type === 'hypothesis') ?? null
  )

  const nodeCount = computed(() => nodes.value.size)
  const maxDepth = computed(() =>
    Array.from(nodes.value.values()).reduce((max, node) => Math.max(max, node.depth), 0)
  )

  function setNode(node: Node) {
    const next = new Map(nodes.value)
    next.set(node.id, node)
    nodes.value = next
  }

  function setEdge(edge: Edge) {
    if (edges.value.some(existing => existing.id === edge.id)) return
    edges.value = [...edges.value, edge]
  }

  function pushFeed(event: NemoEvent) {
    feed.value = [...feed.value, createFeedItem(event)]
  }

  function debug(message: string, details?: Record<string, unknown>) {
    if (details) {
      console.log(`[nemo:client:${sessionId}] ${message}`, details)
      return
    }
    console.log(`[nemo:client:${sessionId}] ${message}`)
  }

  function applyEvent(event: NemoEvent) {
    debug('event:received', { type: event.type })
    if (event.type === 'node:created' || event.type === 'node:updated') {
      setNode(event.node)
      pushFeed(event)
      debug('event:node', {
        type: event.type,
        node_id: event.node.id,
        node_status: event.node.status,
        node_type: event.node.type,
        node_count: nodes.value.size
      })
      return
    }

    if (event.type === 'edge:created') {
      setEdge(event.edge)
      pushFeed(event)
      debug('event:edge', {
        edge_id: event.edge.id,
        edge_type: event.edge.type,
        edge_count: edges.value.length
      })
      return
    }

    if (event.type === 'session:status') {
      if (session.value) {
        session.value = {
          ...session.value,
          status: event.status
        }
      }
      status.value = event.status
      pushFeed(event)
      debug('event:status', { status: event.status })
      return
    }

    error.value = event.error
    pushFeed(event)
    debug('event:error', { message: event.error })
  }

  async function hydrate() {
    isHydrating.value = true
    error.value = null
    debug('hydrate:start')

    try {
      const data = await $fetch<SessionGraphResponse>(`/api/sessions/${encodeURIComponent(sessionId)}`)
      session.value = data.session
      status.value = data.session.status
      nodes.value = new Map(data.nodes.map(node => [node.id, node]))
      edges.value = [...data.edges]
      feed.value = []
      debug('hydrate:success', {
        status: data.session.status,
        node_count: data.nodes.length,
        edge_count: data.edges.length
      })
    } catch (err) {
      error.value = `Failed to hydrate session: ${toErrorMessage(err)}`
      debug('hydrate:failed', { error: toErrorMessage(err) })
      throw err
    } finally {
      isHydrating.value = false
    }
  }

  function disconnect() {
    if (source) {
      source.close()
      source = null
    }
    isConnected.value = false
    debug('stream:disconnect')
  }

  async function connect() {
    if (import.meta.server) return

    disconnect()
    await hydrate()

    debug('stream:connect')
    source = new EventSource(`/api/sessions/${encodeURIComponent(sessionId)}/stream`)
    source.onopen = () => {
      isConnected.value = true
      error.value = null
      debug('stream:open')
    }
    source.onmessage = (message) => {
      if (!message.data) return
      try {
        const parsed = JSON.parse(message.data) as unknown
        if (isNemoEvent(parsed)) {
          applyEvent(parsed)
        } else {
          debug('stream:ignored_non_nemo_event')
        }
      } catch (err) {
        debug('stream:malformed_payload', { error: toErrorMessage(err) })
        // Ignore malformed payloads so one bad event does not break the stream.
      }
    }
    source.onerror = () => {
      isConnected.value = false
      debug('stream:error')
    }
  }

  async function dive() {
    error.value = null
    try {
      await $fetch(`/api/sessions/${encodeURIComponent(sessionId)}/dive`, { method: 'POST' })
      status.value = 'diving'
      if (session.value) {
        session.value = {
          ...session.value,
          status: 'diving'
        }
      }
    } catch (err) {
      if (isAlreadyRunningError(err)) {
        status.value = 'diving'
        if (session.value) {
          session.value = {
            ...session.value,
            status: 'diving'
          }
        }
        return
      }
      error.value = `Failed to start dive: ${toErrorMessage(err)}`
    }
  }

  async function pause() {
    error.value = null
    try {
      await $fetch(`/api/sessions/${encodeURIComponent(sessionId)}/pause`, { method: 'POST' })
      status.value = 'paused'
      if (session.value) {
        session.value = {
          ...session.value,
          status: 'paused'
        }
      }
    } catch (err) {
      error.value = `Failed to pause session: ${toErrorMessage(err)}`
    }
  }

  async function resume() {
    error.value = null
    try {
      await $fetch(`/api/sessions/${encodeURIComponent(sessionId)}/resume`, { method: 'POST' })
      status.value = 'diving'
      if (session.value) {
        session.value = {
          ...session.value,
          status: 'diving'
        }
      }
    } catch (err) {
      if (isAlreadyRunningError(err)) {
        status.value = 'diving'
        if (session.value) {
          session.value = {
            ...session.value,
            status: 'diving'
          }
        }
        return
      }
      error.value = `Failed to resume session: ${toErrorMessage(err)}`
    }
  }

  async function stop() {
    error.value = null
    try {
      await $fetch(`/api/sessions/${encodeURIComponent(sessionId)}/stop`, { method: 'POST' })
      status.value = 'surfaced'
      if (session.value) {
        session.value = {
          ...session.value,
          status: 'surfaced'
        }
      }
    } catch (err) {
      error.value = `Failed to stop session: ${toErrorMessage(err)}`
    }
  }

  onMounted(() => {
    void connect()
  })

  onUnmounted(() => {
    disconnect()
  })

  return {
    session,
    nodes,
    edges,
    status,
    feed,
    isConnected,
    isHydrating,
    error,
    exploringNode,
    frontier,
    syntheses,
    hypothesis,
    nodeCount,
    maxDepth,
    connect,
    disconnect,
    dive,
    pause,
    resume,
    stop
  }
}
