# Nemo

## The Submarine

Nemo is an autonomous data exploration engine. You give it a dataset and a hypothesis — or just a hunch — and it dives. It runs queries, reads the results, generates charts, forms insights, and connects them into a growing knowledge graph. It doesn't stop after one answer. It keeps going, following threads, finding conflicts, surfacing patterns you didn't think to look for.

The core insight: a good analyst doesn't just answer the question they were asked. They follow the thread until they find the question that *should* have been asked. Nemo does that systematically, building a structured map of evidence along the way.

Think of it as a submarine sonar sweep. It pings the dataset, listens for signal, moves toward what's interesting, and builds a map of the terrain as it goes. When it surfaces, you don't get a single answer — you get a connected graph of evidence that tells a story.

---

## What a Session Looks Like

1. User uploads a dataset (CSV, database connection, whatever)
2. Nemo profiles it — columns, types, distributions, relationships
3. User provides a hypothesis: *"The redesign is causing churn"*
4. Nemo dives
5. The graph grows in real time on screen — nodes appearing, edges connecting, charts rendering
6. The user watches, clicks into nodes to see the work, or walks away and comes back
7. Eventually Nemo surfaces a synthesis: *"The redesign isn't causing churn. The pricing change is. But the redesign is actually protecting engaged users from churning."*

The whole time, the user sees every query, every chart, every table, every reasoning step. Full transparency. It's not a black box — it's an analyst working in front of you, just faster and more systematic.

---

## Data Model

### Dataset

The uploaded data that Nemo explores.

```
Dataset {
  id
  name
  description
  source_type          // "csv" | "postgres" | "sqlite"
  connection_info      // file path or connection string
  profile              // auto-generated schema summary for LLM context
    → columns[]
      → name
      → dtype
      → sample_values
      → nulls
      → distribution_summary
    → row_count
    → relationships[]   // detected foreign keys, join paths
  created_at
}
```

The profile is critical. It's the map Nemo carries with it underwater. Every prompt to the LLM includes this so it knows what it can query.

### Session

A single exploration run.

```
Session {
  id
  dataset_id
  hypothesis            // the starting prompt
  context               // optional background (future: org context, docs, etc.)
  status                // "diving" | "reflecting" | "surfaced" | "paused"
  config
    → max_nodes          // stop condition
    → reflect_every      // how often to trigger reflection
    → model              // which LLM
  created_at
  updated_at
}
```

### Node (the core object)

Every node is an insight — a single unit of analysis work.

```
Node {
  id
  session_id
  type                  // "hypothesis" | "insight" | "synthesis"
  status                // "frontier" | "exploring" | "complete" | "dead_end"

  // The work
  question              // what Nemo asked ("Is churn actually up?")
  code                  // the generated SQL query
  result                // structured output from execution
    → type              // "table" | "scalar" | "error"
    → data              // JSON table data, number, or error message
  answer                // LLM interpretation of the result
  confidence            // 0.0 to 1.0

  // Visualization (optional, for chart-worthy results)
  viz_spec              // declarative chart config suggested by LLM
    → kind              // "bar" | "line" | "scatter" | "histogram" | "heatmap" | "pie"
    → x, y              // column mappings
    → group_by          // optional grouping column
    → title             // chart title
  chart_image_url       // server-rendered chart image (for LLM vision input + export)

  // Synthesis nodes only
  summary               // high-level narrative combining child evidence
  supported_by[]        // node IDs that form the evidence base

  // Graph position
  depth                 // distance from root
  priority              // computed score for frontier selection
  
  created_at
}
```

### Edge

The connective tissue. This is where knowledge structure lives.

```
Edge {
  id
  session_id
  source_id             // the node this edge comes FROM
  target_id             // the node this edge points TO
  type                  // "supports" | "conflicts" | "refines" | "inspires"

  // "supports"  — target provides evidence FOR source
  // "conflicts" — target provides evidence AGAINST source
  // "refines"   — target narrows, qualifies, or adds nuance to source
  // "inspires"  — source raised a new question that target explores

  reasoning             // brief LLM explanation of why this edge exists
  created_at
}
```

The edge types matter because they drive both the visual (green/red/yellow/blue) and the logic. Confidence propagation flows through these edges — a hypothesis with three supporting edges from high-confidence nodes is strong. One with a 0.95 conflict is in trouble.

---

## Views

### 1. Home / Sessions List

The entry point. Clean, minimal.

- List of past sessions with hypothesis text, node count, status, last updated
- "New Session" button → opens the setup flow
- Each session card shows a tiny thumbnail of its graph (visual fingerprint)

### 2. New Session Setup

Two-step flow:

**Step 1 — Dataset**
- Upload CSV (drag and drop) or enter database connection
- Nemo profiles it and shows the schema summary
- User can review columns, see sample data, sanity check

**Step 2 — Hypothesis**
- Text input for the hypothesis
- Optional: additional context textarea (future: rich context ingestion)
- "Dive" button launches the session

### 3. Session View (the main event)

This is where you spend 90% of your time. Three panels:

**Left: Graph View**
- Force-directed graph growing in real time
- Nodes colored by type (blue = hypothesis, gray = insight, gold = synthesis)
- Edges colored by type (green = supports, red = conflicts, yellow = refines, blue = inspires)
- Animated pulse on the node currently being explored
- Click a node to select it and open its detail in the right panel
- Zoom, pan, standard graph interactions
- Dead-end nodes are dimmed

**Center: Activity Feed**
- Streaming log of what Nemo is doing right now
- Each entry is compact: "Exploring: Is churn concentrated in mid-tier users?" → "Running query..." → "Found: mid-tier churn is 8.7% vs 3.1% baseline"
- Serves as the "watching an analyst work" experience
- Synthesis/reflect moments are visually distinct (highlighted, expanded)
- User can scroll back through history

**Right: Node Detail Panel**
- Opens when a node is selected in the graph
- Tabs or sections:
  - **Question** — what was asked and why (with link to parent node for context)
  - **Code** — the generated query/script, syntax highlighted
  - **Result** — rendered output:
    - Tables render as clean data tables with sorting
    - Charts render as interactive visualizations (from result data + viz_spec)
    - Scalars render as big numbers with context
    - Errors show the error with Nemo's recovery attempt
  - **Interpretation** — the LLM's answer and confidence score
  - **Connections** — list of edges to/from this node with type and reasoning
- For synthesis nodes: shows the narrative summary and the evidence nodes that support it

**Top Bar**
- Session status indicator (diving / reflecting / surfaced / paused)
- Hypothesis text always visible
- Pause / Resume / Stop controls
- Node count and current depth

### 4. Summary View

Accessible from the session view when Nemo surfaces or when the user wants a snapshot.

- The top-level synthesis narrative
- Key findings as cards, each linked back to their evidence subgraph
- The original hypothesis with a verdict (supported / refuted / complicated / insufficient evidence)
- Exportable as markdown or PDF (future: slides)

### 5. Dataset Explorer (secondary)

A utility view for poking around the dataset directly.

- Schema browser
- Sample data viewer
- Basic column statistics
- Useful for the user to build intuition about the data before launching a session
- Or during a session to manually check something

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│                  Nuxt 3 Frontend                  │
│                                                   │
│  useSession() composable ←── SSE ──→ API Layer    │
│    → reactive nodes, edges, status, feed          │
│    → computed: exploringNode, frontier, syntheses  │
│                                                   │
│  Graph View · Feed · Detail Panel                 │
│    (read from composable, never from engine)      │
└──────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────┐
│                Server (Nitro)                     │
│                                                   │
│  Session Service (glue layer)                     │
│    → wires engine events to SSE stream            │
│    → persists events for replay                   │
│    │                                              │
│    ▼                                              │
│  Engine (pure, no Nuxt imports)                   │
│    │                                              │
│    ├── run(emit) → the outer loop                 │
│    │   Select → Explore → Execute                 │
│    │   → Integrate → Reflect                      │
│    │   emits: node:created, node:updated,         │
│    │          edge:created, session:status         │
│    │                                              │
│    ├── LLM Client (OpenAI Responses API)          │
│    │     → Question generation                    │
│    │     → SQL generation                         │
│    │     → Result interpretation + viz suggestion │
│    │     → Edge classification                    │
│    │     → Synthesis (with chart vision input)    │
│    │                                              │
│    ├── SQL Executor                               │
│    │     → Runs generated SQL against Postgres    │
│    │     → Read-only, statement_timeout enforced  │
│    │     → Returns table / scalar / error         │
│    │                                              │
│    ├── Chart Renderer                             │
│    │     → Generates chart images from viz_spec   │
│    │     → Node.js server-side (no Python)        │
│    │     → Images fed to LLM for visual analysis  │
│    │                                              │
│    └── Graph Store                                │
│          → Nodes + Edges in SQLite                │
│          → Frontier queue                         │
│          → Confidence propagation                 │
│                                                   │
└──────────────────────────────────────────────────┘
```

### Key Technical Decisions

**SQL-only execution**: Generated SQL runs directly against Supabase Postgres in a read-only transaction with `statement_timeout` (default 15s) and `LIMIT` enforcement. No Python, no subprocess, no pandas — keeps deployment serverless-compatible and dependency-light. Results come back as JSON tables or scalars.

**Chart rendering**: Two-tier approach. The LLM suggests a declarative `viz_spec` (kind, axes, grouping) when a result is chart-worthy. The client renders interactive charts from `result.data + viz_spec`. For LLM visual analysis (the model needs to "see" charts), the server generates a static chart image using a Node.js charting library and passes it as vision input. Chart images are cached in Supabase Storage.

**Streaming**: The frontend subscribes to the session via SSE. Every time a node is created or updated, the event fires. The graph animates in real time. The feed scrolls. It feels alive.

**LLM usage per cycle**: Each outer loop iteration involves ~3-4 LLM calls:
1. Select + Explore: "Given this graph state and frontier, what should we investigate next?" → question
2. Execute: "Given this question and dataset schema, write the SQL" → SQL query
3. Integrate: "Given the result, what does this mean? Should we chart it? What edges connect this to existing nodes?" → answer + viz_spec + edges
4. Reflect (periodic): "Look at the full graph + chart images. Synthesize." → synthesis node

**Graph storage**: Supabase Postgres. The graph is small (tens to low hundreds of nodes per session). Nodes and edges tables with simple queries — no graph database needed.

**Frontier priority**: Start simple. Score = (1 - confidence_of_parent) × depth_penalty. Explore uncertain things near the root first. Get fancier later.

---

## Project Structure

```
nemo/
├── nuxt.config.ts
├── package.json
│
├── server/
│   ├── api/
│   │   ├── sessions/
│   │   │   ├── index.post.ts          // create session
│   │   │   ├── [id].get.ts            // get session with graph
│   │   │   ├── [id]/dive.post.ts      // start exploration
│   │   │   ├── [id]/pause.post.ts     // pause
│   │   │   └── [id]/stream.get.ts     // SSE endpoint
│   │   │
│   │   └── datasets/
│   │       ├── index.post.ts          // upload dataset
│   │       └── [id]/profile.get.ts    // get schema profile
│   │
│   ├── services/
│   │   └── session.ts                 // glue: wires engine to SSE + persistence
│   │
│   ├── core/                          // ← pure engine, no Nuxt imports
│   │   ├── engine.ts                  // the outer loop, run(emit)
│   │   ├── executor.ts                // SQL executor (runs queries against Postgres)
│   │   ├── chartRenderer.ts           // server-side chart image generation
│   │   ├── llm.ts                     // OpenAI Responses API client
│   │   ├── graph.ts                   // node/edge operations
│   │   ├── frontier.ts                // priority queue logic
│   │   ├── prompts.ts                 // all LLM prompt templates
│   │   └── types.ts                   // NemoEvent, Node, Edge, etc.
│   │
│   └── db/
│       ├── schema.ts                  // SQLite schema
│       └── migrations/
│
├── components/
│   ├── graph/
│   │   ├── ExplorationGraph.vue       // force-directed graph (d3)
│   │   ├── GraphNode.vue              // node rendering
│   │   └── GraphEdge.vue              // edge rendering
│   │
│   ├── session/
│   │   ├── ActivityFeed.vue           // streaming log
│   │   ├── NodeDetail.vue             // right panel
│   │   ├── NodeResult.vue             // table/chart/scalar renderer
│   │   ├── SessionControls.vue        // pause/resume/stop
│   │   └── SynthesisSummary.vue       // reflect output
│   │
│   └── dataset/
│       ├── DatasetUpload.vue          // upload + profile
│       └── SchemaViewer.vue           // column browser
│
├── pages/
│   ├── index.vue                      // sessions list
│   ├── new.vue                        // new session setup
│   └── session/
│       └── [id].vue                   // main session view
│
└── composables/
    └── useSession.ts                  // reactive session state + SSE consumer
```

---

## Engine Decoupling

The hardest design decision in Nemo is keeping the engine and the frontend cleanly separated while still making the UI feel alive. The rule is simple:

> **The engine only knows about the graph. The frontend only knows about events.**

### The Boundary

The engine is a pure state machine. It takes a graph and produces a new graph. It never thinks about rendering, SSE, WebSockets, or UI state. Its entire interface is:

- **Input:** a graph (nodes + edges) + a dataset profile + a hypothesis
- **Output:** a stream of events describing what changed

The test for clean decoupling: you can run the engine from a standalone CLI script with zero frontend code. If `engine.run(console.log)` works and prints a meaningful exploration, the architecture is right.

### Event Types

The engine emits a small set of typed events. These are the only contract between engine and frontend:

```
node:created     — new node added to graph (frontier, or synthesis)
node:updated     — node changed (status, code, result, answer, confidence)
edge:created     — new relationship between two nodes
session:status   — engine status change (diving, reflecting, paused, surfaced)
session:error    — something went wrong (query timeout, LLM error, etc.)
```

The frontend derives ALL visual state from these events. "Which node is pulsing blue?" — the one whose last `node:updated` set `status: 'exploring'`. "Is the engine running?" — the last `session:status` event. No special UI channels, no sidecar state.

### Engine Core

The engine's run method takes a single callback. It doesn't import anything from Nuxt, doesn't know about HTTP, doesn't know it's being visualized:

```typescript
// server/core/engine.ts

type NemoEvent =
  | { type: 'node:created'; node: Node }
  | { type: 'node:updated'; node: Node }
  | { type: 'edge:created'; edge: Edge }
  | { type: 'session:status'; status: SessionStatus }
  | { type: 'session:error'; error: string }

class NemoEngine {
  constructor(
    private graph: GraphStore,
    private dataset: DatasetProfile,
    private llm: LLMClient,
    private executor: SqlExecutor,
    private chartRenderer: ChartRenderer,
    private config: SessionConfig
  ) {}

  async run(emit: (event: NemoEvent) => void) {
    emit({ type: 'session:status', status: 'diving' })

    while (this.status === 'diving') {
      // 1. Select
      const next = await this.graph.selectFrontier()
      if (!next) { break }

      emit({ type: 'node:updated', node: { ...next, status: 'exploring' } })

      // 2. Explore + Execute
      const question = await this.llm.generateQuestion(next, this.graph)
      const sql = await this.llm.generateSQL(question, this.dataset)
      const result = await this.executor.run(sql)
      const interpretation = await this.llm.interpret(result, question)

      // 3. Visualize (if chart-worthy)
      let chartImageUrl = null
      if (interpretation.vizSpec) {
        chartImageUrl = await this.chartRenderer.render(result, interpretation.vizSpec)
      }

      // 4. Integrate
      const completed = { ...next, question, code: sql, result, ...interpretation, chart_image_url: chartImageUrl, status: 'complete' }
      await this.graph.updateNode(completed)
      emit({ type: 'node:updated', node: completed })

      const edges = await this.llm.classifyEdges(completed, this.graph)
      for (const edge of edges) {
        await this.graph.createEdge(edge)
        emit({ type: 'edge:created', edge })
      }

      // Generate new frontier nodes
      const newNodes = await this.llm.suggestNext(completed, this.graph)
      for (const node of newNodes) {
        await this.graph.createNode(node)
        emit({ type: 'node:created', node })
      }

      // 5. Reflect (periodic)
      if (this.shouldReflect()) {
        emit({ type: 'session:status', status: 'reflecting' })
        const synthesis = await this.reflect()  // can include chart images as vision input
        emit({ type: 'node:created', node: synthesis })
        emit({ type: 'session:status', status: 'diving' })
      }

      // Stop conditions
      if (this.graph.nodeCount() >= this.config.maxNodes) break
    }

    emit({ type: 'session:status', status: 'surfaced' })
  }
}
```

### Session Service (the glue)

A thin orchestration layer that wires the engine to an SSE stream. This is the only place that knows about both the engine and HTTP:

```typescript
// server/services/session.ts

async function startSession(sessionId: string, res: ServerResponse) {
  const session = await db.getSession(sessionId)
  const graph = new GraphStore(sessionId)
  const engine = new NemoEngine(graph, session.dataset, llm, executor, session.config)

  const stream = createSSEStream(res)

  await engine.run((event) => {
    stream.push(event)              // send to connected client
    db.appendEvent(sessionId, event) // persist for replay
  })
}
```

Persisting events to the database means a client that connects late (or reconnects after a drop) can replay the full event history to reconstruct current state. The SSE endpoint handles this:

```typescript
// server/api/sessions/[id]/stream.get.ts

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id')
  const stream = createSSEStream(event)

  // Replay past events to catch up
  const history = await db.getEvents(id)
  for (const e of history) {
    stream.push(e)
  }

  // Then stream live events
  await startSession(id, stream)
})
```

### Client Composable

The frontend consumes the SSE stream through a single composable. No Pinia — just reactive refs that update as events arrive:

```typescript
// composables/useSession.ts

export function useSession(sessionId: string) {
  const nodes = ref<Map<string, Node>>(new Map())
  const edges = ref<Edge[]>([])
  const status = ref<SessionStatus>('idle')
  const feed = ref<FeedItem[]>([])

  // Derived state — the frontend figures out what to show
  const exploringNode = computed(() =>
    [...nodes.value.values()].find(n => n.status === 'exploring')
  )
  const frontier = computed(() =>
    [...nodes.value.values()].filter(n => n.status === 'frontier')
  )
  const syntheses = computed(() =>
    [...nodes.value.values()].filter(n => n.type === 'synthesis')
  )
  const hypothesis = computed(() =>
    [...nodes.value.values()].find(n => n.type === 'hypothesis')
  )
  const rootConfidence = computed(() => {
    const h = hypothesis.value
    if (!h) return null
    // Aggregate confidence from supporting/conflicting edges
    // ... confidence propagation logic
  })

  let eventSource: EventSource | null = null

  function connect() {
    eventSource = new EventSource(`/api/sessions/${sessionId}/stream`)

    eventSource.onmessage = (e) => {
      const event: NemoEvent = JSON.parse(e.data)

      switch (event.type) {
        case 'node:created':
          nodes.value.set(event.node.id, event.node)
          feed.value.push(toFeedItem(event))
          break

        case 'node:updated':
          nodes.value.set(event.node.id, event.node)
          feed.value.push(toFeedItem(event))
          break

        case 'edge:created':
          edges.value.push(event.edge)
          break

        case 'session:status':
          status.value = event.status
          feed.value.push(toFeedItem(event))
          break
      }
    }
  }

  function disconnect() {
    eventSource?.close()
  }

  // Actions — thin HTTP calls
  async function pause() {
    await $fetch(`/api/sessions/${sessionId}/pause`, { method: 'POST' })
  }
  async function resume() {
    await $fetch(`/api/sessions/${sessionId}/resume`, { method: 'POST' })
  }

  onMounted(connect)
  onUnmounted(disconnect)

  return {
    // State
    nodes,
    edges,
    status,
    feed,

    // Derived
    exploringNode,
    frontier,
    syntheses,
    hypothesis,
    rootConfidence,

    // Actions
    pause,
    resume,
  }
}
```

### Why This Works

**The engine is testable in isolation.** Write a test that feeds it a CSV and a hypothesis, collects the emitted events, and asserts on the graph structure. No HTTP, no browser, no Vue.

**The frontend is testable in isolation.** Feed mock events into `useSession` and assert that the derived state (exploring node, frontier, feed items) is correct. No engine, no LLM, no database.

**The event log is the source of truth.** If something goes wrong, you can replay the event stream to reconstruct exactly what happened. You can also replay it at 10x speed in the UI for a "session recap" feature later.

**The engine is portable.** Today it runs in a Nitro server handler. Tomorrow it could run in a worker thread, a separate process, or a cloud function. The interface is just `run(callback)` — move it anywhere.

**The composable is the only place that knows about both SSE and Vue reactivity.** Components never subscribe to events directly. They just read reactive state from `useSession()`. If you swap SSE for WebSockets later, only the composable changes.

---

## What v0 Is Not (yet)

- Not multi-user
- Not multi-dataset joins (one dataset per session)
- Not parallel execution
- Not self-hosting the LLM
- Not a notebook (no manual cell editing)
- Not a BI tool (no saved dashboards)

It's a submarine. It dives, it explores, it surfaces with a map of what it found. That's it.