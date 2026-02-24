# Nemo Implementation Plan

**Date:** 2026-02-24  
**Status:** In Progress (Sprint 1 foundations completed)  
**Author:** AI Assistant

## Overview

Transform the current Nuxt UI dashboard template into Nemo — an autonomous data exploration engine. Users upload a dataset, provide a hypothesis, and Nemo dives: running queries, generating charts, forming insights, and building a connected knowledge graph in real time.

### Goals
- Replace demo CRM pages with Nemo-specific views (sessions, datasets, exploration graph)
- Build the core engine loop (select → explore → execute → integrate → reflect) as a pure, decoupled module
- Stream exploration progress to the frontend via SSE so the graph grows live on screen
- Render every query, chart, table, and reasoning step — full transparency, zero black box

### Current State
The project has a working Nuxt 4 scaffold with Nuxt UI, layouts, routing, color mode, and keyboard shortcuts. All pages (customers, inbox, settings) and API routes serve hardcoded mock data. The dashboard layout, landing page, and component patterns are reusable — the plumbing is solid, the content needs to be gutted and rebuilt.

---

## Sprint 1: Project Structure + Data Layer

Lay the foundation: define types, set up Supabase (Postgres), build dataset ingestion, and scaffold the page structure that will carry through every subsequent sprint.

### 1.1 Clean Up Demo Content [✅ Completed]

Remove the CRM placeholder pages and components. Keep the layout shell, navigation patterns, and shared utilities.

**Remove:**
- `pages/home.vue`, `pages/customers.vue`, `pages/inbox.vue`, `pages/settings.vue`, `pages/settings/*`
- `components/customers/*`, `components/home/*`, `components/inbox/*`, `components/settings/*`
- `server/api/customers.ts`, `server/api/mails.ts`, `server/api/members.ts`, `server/api/notifications.ts`
- `components/NotificationsSlideover.vue`
- `app/types/index.d.ts` (replace entirely)
- `app/utils/index.ts` (replace)

**Keep and adapt:**
- `layouts/default.vue` — update navigation to Sessions, Datasets, Settings
- `layouts/landing.vue` — keep for landing page
- `components/NemoLogo.vue`, `components/UserMenu.vue`, `components/TeamsMenu.vue`
- `composables/useDashboard.ts` — gut and repurpose as `useApp.ts`
- `pages/index.vue` — keep landing page, update copy for Nemo

### 1.2 Core Type Definitions [✅ Completed]

**File:** `server/core/types.ts`

All shared types live here. The engine, APIs, and frontend all import from this single source.

```typescript
// Dataset
export interface DatasetProfile {
  columns: ColumnProfile[]
  row_count: number
  relationships: DetectedRelationship[]
}

export interface ColumnProfile {
  name: string
  dtype: string
  sample_values: any[]
  nulls: number
  distribution_summary: string
}

export interface DetectedRelationship {
  from_column: string
  to_column: string
  type: string
}

// Session
export type SessionStatus = 'idle' | 'diving' | 'reflecting' | 'surfaced' | 'paused'

export interface SessionConfig {
  max_nodes: number
  reflect_every: number
  model: string
}

// Node
export type NodeType = 'hypothesis' | 'insight' | 'synthesis'
export type NodeStatus = 'frontier' | 'exploring' | 'complete' | 'dead_end'

export interface NodeResult {
  type: 'table' | 'chart' | 'scalar' | 'error'
  data: any // JSON table, Plotly spec, number, or error string
}

export interface Node {
  id: string
  session_id: string
  type: NodeType
  status: NodeStatus
  question: string | null
  code: string | null
  result: NodeResult | null
  answer: string | null
  confidence: number | null
  summary: string | null        // synthesis nodes only
  supported_by: string[] | null // synthesis nodes only
  depth: number
  priority: number
  created_at: string
}

// Edge
export type EdgeType = 'supports' | 'conflicts' | 'refines' | 'inspires'

export interface Edge {
  id: string
  session_id: string
  source_id: string
  target_id: string
  type: EdgeType
  reasoning: string | null
  created_at: string
}

// Events (engine → frontend contract)
export type NemoEvent =
  | { type: 'node:created'; node: Node }
  | { type: 'node:updated'; node: Node }
  | { type: 'edge:created'; edge: Edge }
  | { type: 'session:status'; status: SessionStatus }
  | { type: 'session:error'; error: string }

// Feed (frontend display)
export interface FeedItem {
  id: string
  timestamp: string
  event_type: NemoEvent['type']
  title: string
  detail: string | null
  node_id: string | null
}
```

**File:** `app/types/index.d.ts`

Re-export server types for frontend use plus any UI-only types.

```typescript
export type { 
  Node, Edge, NodeType, NodeStatus, EdgeType,
  SessionStatus, SessionConfig, DatasetProfile,
  NemoEvent, FeedItem, NodeResult
} from '~~/server/core/types'

export interface Dataset {
  id: string
  name: string
  description: string | null
  source_type: 'csv' | 'postgres' | 'sqlite'
  row_count: number | null
  column_count: number | null
  created_at: string
}

export interface Session {
  id: string
  dataset_id: string
  hypothesis: string
  context: string | null
  status: SessionStatus
  config: SessionConfig
  node_count: number
  max_depth: number
  created_at: string
  updated_at: string
}
```

### 1.3 Supabase Database Setup [✅ Completed]

Use Supabase Postgres for persistent storage. All schema changes are handled through SQL migrations in `db_migrations/`.

**File:** `db_migrations/20260224_001_initial_nemo_schema.sql`

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS datasets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  description TEXT,
  source_type TEXT NOT NULL DEFAULT 'csv',
  connection_info TEXT NOT NULL,  -- storage path or external connection string
  profile JSONB,                  -- JSON: DatasetProfile
  row_count INTEGER,
  column_count INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  hypothesis TEXT NOT NULL,
  context TEXT,
  status TEXT NOT NULL DEFAULT 'idle',
  config JSONB NOT NULL,          -- JSON: SessionConfig
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nodes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'frontier',
  question TEXT,
  code TEXT,
  result JSONB,                   -- JSON: NodeResult
  answer TEXT,
  confidence DOUBLE PRECISION,
  summary TEXT,
  supported_by JSONB,             -- JSON: string[]
  depth INTEGER NOT NULL DEFAULT 0,
  priority DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS edges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  target_id UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  reasoning TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  payload JSONB NOT NULL,          -- JSON: NemoEvent
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_nodes_session ON nodes(session_id);
CREATE INDEX idx_edges_session ON edges(session_id);
CREATE INDEX idx_events_session ON events(session_id, id);
```

**File:** `server/services/supabase.ts`

Supabase server client initialization and typed data access helpers for datasets, sessions, nodes, edges, and events.

### 1.4 Dataset Upload + Profiling [✅ Completed]

**File:** `server/api/datasets/index.post.ts`

Accept CSV upload via multipart form data. Save to Supabase Storage (`datasets` bucket), parse with a CSV library, generate a `DatasetProfile` (column types, sample values, null counts, basic distribution stats), and persist metadata/profile to Postgres.

**File:** `server/api/datasets/[id]/profile.get.ts`

Return the stored profile for a dataset.

**File:** `server/api/datasets/index.get.ts`

List all datasets.

### 1.5 Scaffold New Pages [✅ Completed]

Create stub pages wired into routing. Content comes in later sprints.

| Page | Route | Purpose |
|------|-------|---------|
| `pages/index.vue` | `/` | Landing page (update existing) |
| `pages/sessions.vue` | `/sessions` | Sessions list (home when logged in) |
| `pages/sessions/new.vue` | `/sessions/new` | New session setup |
| `pages/sessions/[id].vue` | `/sessions/:id` | Session view (main event) |
| `pages/datasets.vue` | `/datasets` | Dataset library |

Update `layouts/default.vue` navigation:
- Sessions (home icon)
- Datasets (database icon)
- Settings (cog icon)

### Sprint 1 Deliverables
- [x] Demo CRM content removed
- [x] Core types defined in `server/core/types.ts`
- [x] Supabase migration created and applied via `db_migrations/`
- [x] Dataset upload API accepts CSV and generates profile
- [x] Dataset list and profile retrieval APIs work
- [x] Page stubs exist for all routes with updated navigation
- [x] Layout updated with Nemo-specific sidebar links

---

## Sprint 2: Engine Core + Execution

Build the exploration engine as a pure module with zero Nuxt imports. It takes a graph, a dataset profile, and a hypothesis — and produces a stream of events. Testable from a standalone script.

### 2.1 LLM Client

**File:** `server/core/llm.ts`

Thin wrapper around the Anthropic Claude API. Each method maps to a specific role in the exploration loop.

```typescript
class LLMClient {
  constructor(private apiKey: string, private model: string) {}

  // Given graph state + frontier node, generate the next question to investigate
  async generateQuestion(node: Node, graphContext: GraphContext): Promise<string>

  // Given a question + dataset schema, generate executable Python/SQL
  async generateCode(question: string, profile: DatasetProfile): Promise<string>

  // Given execution result + original question, interpret the finding
  async interpret(result: NodeResult, question: string): Promise<{
    answer: string
    confidence: number
  }>

  // Given a completed node + graph, classify edges to existing nodes
  async classifyEdges(node: Node, graphContext: GraphContext): Promise<Omit<Edge, 'id' | 'created_at'>[]>

  // Given a completed node + graph, suggest 1-3 follow-up frontier nodes
  async suggestNext(node: Node, graphContext: GraphContext): Promise<Omit<Node, 'id' | 'created_at'>[]>

  // Given the full graph, synthesize findings into a narrative
  async synthesize(graphContext: GraphContext, hypothesis: string): Promise<{
    summary: string
    confidence: number
    supported_by: string[]
  }>
}
```

### 2.2 Prompt Templates

**File:** `server/core/prompts.ts`

All LLM prompt templates centralized. Each function returns a structured prompt (system + user messages) for a specific engine step. Includes the dataset profile, current graph state serialization, and clear output format instructions.

Key prompts:
- `questionPrompt` — frontier selection + question generation
- `codePrompt` — Python/SQL generation with schema context
- `interpretPrompt` — result analysis + confidence scoring
- `edgePrompt` — relationship classification between nodes
- `nextPrompt` — follow-up question generation
- `synthesisPrompt` — periodic reflection and narrative building

### 2.3 Code Executor

**File:** `server/core/executor.ts`

Runs LLM-generated Python in a sandboxed subprocess. The dataset is pre-loaded as a pandas DataFrame.

```typescript
class CodeExecutor {
  constructor(private config: { timeout: number; dataDir: string }) {}

  // Execute Python code with the dataset available as `df`
  // Returns structured output: table, chart (Plotly JSON), scalar, or error
  async run(code: string, datasetPath: string): Promise<NodeResult>
}
```

Implementation:
- Spawn a Python subprocess with a wrapper script that:
  - Loads the dataset into a pandas DataFrame (`df`)
  - Executes the generated code
  - Captures output: if the result is a DataFrame → serialize as JSON table; if a Plotly figure → serialize as JSON spec; if a scalar → wrap as `{ type: 'scalar', data: value }`
  - Catches exceptions → `{ type: 'error', data: error_message }`
- Enforces a timeout (default 30s) to prevent runaway queries
- Captures stdout/stderr for debugging

**File:** `server/core/executor_wrapper.py`

The Python harness script that loads data, runs code, and outputs structured JSON.

### 2.4 Graph Store

**File:** `server/core/graph.ts`

In-memory + Supabase graph operations. The engine interacts with this, not raw SQL.

```typescript
class GraphStore {
  constructor(private db: Database, private sessionId: string) {}

  // Node operations
  async createNode(node: Omit<Node, 'id' | 'created_at'>): Promise<Node>
  async updateNode(id: string, updates: Partial<Node>): Promise<Node>
  async getNode(id: string): Promise<Node | null>
  async getNodes(): Promise<Node[]>

  // Edge operations
  async createEdge(edge: Omit<Edge, 'id' | 'created_at'>): Promise<Edge>
  async getEdges(): Promise<Edge[]>
  async getEdgesForNode(nodeId: string): Promise<Edge[]>

  // Frontier
  async selectFrontier(): Promise<Node | null>  // highest priority frontier node

  // Context (for LLM prompts)
  async getGraphContext(): Promise<GraphContext>  // serialized graph state

  // Stats
  nodeCount(): number
  maxDepth(): number
}
```

### 2.5 Frontier Priority

**File:** `server/core/frontier.ts`

Simple priority scoring for frontier selection. Start conservative, get smarter later.

```
priority = (1 - parent_confidence) × depth_penalty(depth)
```

- Low-confidence parents produce high-priority children (investigate uncertainty first)
- Depth penalty decays with distance from root (prefer breadth near the top)
- Dead-end siblings reduce priority (don't keep hitting the same wall)

### 2.6 Engine Outer Loop

**File:** `server/core/engine.ts`

The core loop. Pure state machine, no Nuxt imports. Its only output interface is `emit(event)`.

```typescript
class NemoEngine {
  constructor(
    private graph: GraphStore,
    private dataset: DatasetProfile,
    private datasetPath: string,
    private llm: LLMClient,
    private executor: CodeExecutor,
    private config: SessionConfig
  ) {}

  private status: SessionStatus = 'idle'

  async run(emit: (event: NemoEvent) => void): Promise<void> {
    this.status = 'diving'
    emit({ type: 'session:status', status: 'diving' })

    let iterations = 0
    while (this.status === 'diving') {
      const next = await this.graph.selectFrontier()
      if (!next) break

      // Explore
      emit({ type: 'node:updated', node: { ...next, status: 'exploring' } })
      const question = await this.llm.generateQuestion(next, await this.graph.getGraphContext())
      const code = await this.llm.generateCode(question, this.dataset)
      const result = await this.executor.run(code, this.datasetPath)
      const interpretation = await this.llm.interpret(result, question)

      // Integrate
      const completed = await this.graph.updateNode(next.id, {
        question, code, result,
        answer: interpretation.answer,
        confidence: interpretation.confidence,
        status: 'complete'
      })
      emit({ type: 'node:updated', node: completed })

      const edges = await this.llm.classifyEdges(completed, await this.graph.getGraphContext())
      for (const edgeData of edges) {
        const edge = await this.graph.createEdge(edgeData)
        emit({ type: 'edge:created', edge })
      }

      const newNodes = await this.llm.suggestNext(completed, await this.graph.getGraphContext())
      for (const nodeData of newNodes) {
        const node = await this.graph.createNode(nodeData)
        emit({ type: 'node:created', node })
      }

      // Reflect periodically
      iterations++
      if (iterations % this.config.reflect_every === 0) {
        emit({ type: 'session:status', status: 'reflecting' })
        const synthesis = await this.llm.synthesize(
          await this.graph.getGraphContext(),
          /* hypothesis from root node */
        )
        const synthNode = await this.graph.createNode({
          session_id: next.session_id,
          type: 'synthesis',
          status: 'complete',
          summary: synthesis.summary,
          confidence: synthesis.confidence,
          supported_by: synthesis.supported_by,
          depth: 0,
          priority: 0,
          question: null, code: null, result: null, answer: null
        })
        emit({ type: 'node:created', node: synthNode })
        emit({ type: 'session:status', status: 'diving' })
      }

      // Stop conditions
      if (this.graph.nodeCount() >= this.config.max_nodes) break
    }

    this.status = 'surfaced'
    emit({ type: 'session:status', status: 'surfaced' })
  }

  pause() { this.status = 'paused' }
  resume() { this.status = 'diving' }
  stop() { this.status = 'surfaced' }
}
```

### Sprint 2 Deliverables
- [ ] `LLMClient` wraps Claude API with all six methods
- [ ] Prompt templates produce well-structured, consistent LLM inputs
- [ ] `CodeExecutor` runs Python in subprocess with pandas/plotly, returns structured results
- [ ] `GraphStore` handles all node/edge CRUD and frontier selection
- [ ] Frontier priority scoring works correctly
- [ ] `NemoEngine.run(emit)` executes the full loop and emits typed events
- [ ] Engine can run standalone (e.g., `engine.run(console.log)` in a test script)

---

## Sprint 3: Session APIs + Real-time Streaming

Wire the engine to the HTTP layer. Session CRUD, SSE streaming, event persistence, and the frontend composable that makes the graph feel alive.

### 3.1 Session CRUD

**File:** `server/api/sessions/index.post.ts`

Create a new session. Accepts `dataset_id`, `hypothesis`, optional `context`, and optional `config` overrides. Creates the root hypothesis node. Returns the session object.

**File:** `server/api/sessions/index.get.ts`

List all sessions with summary info (hypothesis, status, node count, last updated).

**File:** `server/api/sessions/[id].get.ts`

Get a single session with its full graph (all nodes and edges). Used for initial page load before the SSE stream connects.

### 3.2 Session Controls

**File:** `server/api/sessions/[id]/dive.post.ts`

Start (or restart) the engine for a session. Initializes the engine, wires it to event persistence, and begins the exploration loop.

**File:** `server/api/sessions/[id]/pause.post.ts`

Pause the running engine. It finishes the current node, then stops.

**File:** `server/api/sessions/[id]/resume.post.ts`

Resume a paused session. Re-initializes the engine from persisted state and continues.

### 3.3 SSE Streaming

**File:** `server/api/sessions/[id]/stream.get.ts`

The real-time connection. Returns an SSE stream that:
1. Replays all past events for the session (so late-joining clients catch up)
2. Then streams live events as the engine produces them

```typescript
export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id')
  const stream = createEventStream(event)

  // Replay history
  const history = await db.getEvents(id)
  for (const e of history) {
    stream.push(JSON.stringify(e.payload))
  }

  // Subscribe to live events
  const unsubscribe = sessionManager.subscribe(id, (nemoEvent) => {
    stream.push(JSON.stringify(nemoEvent))
  })

  stream.onClosed(() => unsubscribe())
  return stream.send()
})
```

### 3.4 Session Service (Glue Layer)

**File:** `server/services/session.ts`

The orchestration layer that wires the engine to persistence and subscribers. Manages active engine instances and their event buses.

```typescript
class SessionManager {
  private engines: Map<string, NemoEngine> = new Map()
  private subscribers: Map<string, Set<(event: NemoEvent) => void>> = new Map()

  async startSession(sessionId: string): Promise<void>
  async pauseSession(sessionId: string): Promise<void>
  async resumeSession(sessionId: string): Promise<void>

  subscribe(sessionId: string, callback: (event: NemoEvent) => void): () => void

  private emit(sessionId: string, event: NemoEvent): void {
    db.appendEvent(sessionId, event)     // persist
    this.subscribers.get(sessionId)?.forEach(cb => cb(event))  // broadcast
  }
}
```

Singleton instance exported for use by API routes.

### 3.5 Frontend Composable

**File:** `app/composables/useSession.ts`

The client-side SSE consumer. Reactive state that updates as events arrive. Components read from this composable and never subscribe to events directly.

```typescript
export function useSession(sessionId: string) {
  const nodes = ref<Map<string, Node>>(new Map())
  const edges = ref<Edge[]>([])
  const status = ref<SessionStatus>('idle')
  const feed = ref<FeedItem[]>([])

  // Derived state
  const exploringNode = computed(/* node with status 'exploring' */)
  const frontier = computed(/* nodes with status 'frontier' */)
  const syntheses = computed(/* nodes with type 'synthesis' */)
  const hypothesis = computed(/* node with type 'hypothesis' */)
  const nodeCount = computed(/* total nodes */)
  const maxDepth = computed(/* max depth across all nodes */)

  // SSE connection management
  function connect() { /* EventSource → parse events → update refs */ }
  function disconnect() { /* close EventSource */ }

  // Actions
  async function dive() { /* POST /api/sessions/:id/dive */ }
  async function pause() { /* POST /api/sessions/:id/pause */ }
  async function resume() { /* POST /api/sessions/:id/resume */ }

  onMounted(connect)
  onUnmounted(disconnect)

  return { nodes, edges, status, feed, exploringNode, frontier, syntheses, hypothesis, nodeCount, maxDepth, dive, pause, resume }
}
```

### Sprint 3 Deliverables
- [ ] Session CRUD APIs (create, list, get with full graph)
- [ ] Dive/pause/resume control endpoints work
- [ ] SSE stream replays history then streams live events
- [ ] `SessionManager` singleton manages engine lifecycle and event fanout
- [ ] Events persisted to Supabase for replay on reconnect
- [ ] `useSession` composable connects, parses events, and maintains reactive state
- [ ] End-to-end test: create session → dive → events stream to client

---

## Sprint 4: Session View Frontend

Build the main event — the three-panel session view where you watch Nemo think. Graph visualization, activity feed, node detail, and session controls.

### 4.1 Session Page Layout

**File:** `app/pages/sessions/[id].vue`

Three-panel layout using the dashboard layout:

```
┌─────────────────────────────────────────────────────────┐
│  Top Bar: status · hypothesis · controls · node count   │
├──────────────┬──────────────────┬───────────────────────┤
│              │                  │                       │
│  Graph View  │  Activity Feed   │  Node Detail Panel    │
│  (left)      │  (center)        │  (right, conditional) │
│              │                  │                       │
└──────────────┴──────────────────┴───────────────────────┘
```

Uses `useSession()` composable for all state. Selecting a node in the graph opens the detail panel.

### 4.2 Graph Visualization

**File:** `app/components/graph/ExplorationGraph.vue`

Force-directed graph rendered with D3. The centerpiece of the UI.

- Nodes colored by type: blue (hypothesis), neutral (insight), amber (synthesis)
- Edges colored by type: green (supports), red (conflicts), yellow (refines), blue (inspires)
- Animated pulse on the currently exploring node
- Dead-end nodes dimmed (reduced opacity)
- Click node → emit `select` event → open detail panel
- Zoom + pan via D3 zoom behavior
- Smooth transitions when new nodes/edges appear (animated entry)
- Force simulation restarts gently when graph grows (no jarring jumps)

**File:** `app/components/graph/GraphNode.vue` — SVG node rendering (circle + label)

**File:** `app/components/graph/GraphEdge.vue` — SVG edge rendering (line + arrow + optional label)

### 4.3 Activity Feed

**File:** `app/components/session/ActivityFeed.vue`

Streaming log of what Nemo is doing. Reads from `feed` ref in `useSession()`.

- Each entry: icon + title + optional detail
- Compact by default, expandable
- Synthesis/reflection moments visually distinct (highlighted card, larger)
- Auto-scrolls to bottom as new entries arrive (with scroll-lock override if user scrolls up)
- Status changes show as dividers ("Diving...", "Reflecting...", "Surfaced")

### 4.4 Node Detail Panel

**File:** `app/components/session/NodeDetail.vue`

Right panel that opens when a node is selected. Uses Nuxt UI tabs.

**Tabs:**
1. **Question** — what was asked, why, link to parent node
2. **Code** — syntax-highlighted generated Python/SQL (use Shiki or similar)
3. **Result** — rendered output via `NodeResult.vue`
4. **Interpretation** — LLM answer text + confidence score badge
5. **Connections** — list of edges to/from this node with type, target node, and reasoning

For synthesis nodes: shows summary narrative and evidence list instead of code/result tabs.

**File:** `app/components/session/NodeResult.vue`

Renders the `NodeResult` based on its type:

| Type | Rendering |
|------|-----------|
| `table` | `UTable` with sorting, scrollable |
| `chart` | Plotly chart rendered interactively (use `plotly.js-dist-min`) |
| `scalar` | Large number with label and context |
| `error` | Error message with red styling |

### 4.5 Session Controls

**File:** `app/components/session/SessionControls.vue`

Top bar controls:
- Status badge (diving = blue pulse, reflecting = amber, surfaced = green, paused = neutral)
- Pause button (visible when diving/reflecting)
- Resume button (visible when paused)
- Stop button (always visible while running)
- Node count display
- Current depth display

### 4.6 Synthesis Summary

**File:** `app/components/session/SynthesisSummary.vue`

Rendered for synthesis nodes in the detail panel and in the summary view. Shows:
- Narrative text
- Confidence score
- Evidence list (links to supporting nodes)

### Sprint 4 Deliverables
- [ ] Session page renders three-panel layout
- [ ] D3 force-directed graph renders nodes and edges with correct colors
- [ ] Graph animates smoothly as new nodes/edges stream in
- [ ] Clicking a node opens the detail panel
- [ ] Currently exploring node pulses
- [ ] Activity feed streams entries and auto-scrolls
- [ ] Node detail panel shows all tabs (question, code, result, interpretation, connections)
- [ ] Result renderer handles tables, Plotly charts, scalars, and errors
- [ ] Session controls (pause/resume/stop) work end-to-end
- [ ] Top bar shows live status, hypothesis, and node count

---

## Sprint 5: Remaining Views + Polish

Build the remaining pages, wire up the new session flow end-to-end, and polish the experience.

### 5.1 Sessions List (Home)

**File:** `app/pages/sessions.vue`

The entry point after the landing page. Clean list of past sessions.

- Session cards showing: hypothesis text, status badge, node count, last updated
- Thumbnail graph preview per session (miniature rendering of the graph, or a static SVG snapshot)
- "New Session" button → navigates to `/sessions/new`
- Empty state with onboarding prompt
- Sort by recent / status filter

### 5.2 New Session Setup

**File:** `app/pages/sessions/new.vue`

Two-step flow using a `UStepper` or tab-based progression.

**Step 1 — Dataset:**
- Select existing dataset from library, or upload new CSV
- Drag-and-drop upload zone
- After upload/selection: show schema summary (column names, types, sample values, row count)
- User can review and confirm

**Step 2 — Hypothesis:**
- Large text input for the hypothesis
- Optional context textarea (background info, what they already know)
- Configuration controls (collapsible "Advanced"):
  - Max nodes (default 50)
  - Reflection frequency (default every 5 nodes)
  - Model selection (default Claude Sonnet)
- "Dive" button → creates session → navigates to session view → auto-starts engine

### 5.3 Summary View

**File:** `app/components/session/SummaryView.vue`

Accessible from the session view when status is `surfaced`, or via a tab/button at any time.

- Top-level synthesis narrative (from the last synthesis node)
- Key findings as cards, each with:
  - Finding title (from node question/answer)
  - Confidence badge
  - Click to jump to that node in the graph
- Original hypothesis with verdict: **Supported** / **Refuted** / **Complicated** / **Insufficient Evidence**
- Export as markdown (copy to clipboard or download)

### 5.4 Dataset Explorer

**File:** `app/pages/datasets.vue`

Utility view for browsing datasets.

- List of uploaded datasets with name, row count, column count, upload date
- Click to expand: schema browser showing all columns with types and stats
- Sample data viewer (first N rows as a table)
- Basic column statistics (nulls, unique values, min/max for numerics)
- Delete dataset option

### 5.5 Update Landing Page

**File:** `app/pages/index.vue`

Update the existing landing page copy to accurately describe Nemo:
- Hero: "Give it a dataset and a hypothesis. It dives."
- Feature cards aligned with actual capabilities
- CTA → "Start Exploring" links to `/sessions/new`

### 5.6 Polish + Error Handling

- **Responsive design:** Ensure session view degrades gracefully on smaller screens (stack panels vertically on mobile, collapsible panels on tablet)
- **Error boundaries:** Handle engine errors gracefully (LLM failures, execution timeouts, malformed data)
- **Loading states:** Skeleton loaders for session page initial load, graph area, and feed
- **Reconnection:** `useSession` composable auto-reconnects SSE on connection drop with exponential backoff
- **Keyboard shortcuts:** Update command palette for Nemo actions (new session, pause/resume, navigate sessions)
- **Empty states:** Meaningful empty states for no sessions, no datasets, empty graph
- **Toast notifications:** Surface important events (session surfaced, errors) as toasts

### Sprint 5 Deliverables
- [ ] Sessions list page with session cards and graph thumbnails
- [ ] New session setup flow works end-to-end (upload → hypothesis → dive)
- [ ] Summary view renders synthesis, key findings, and verdict
- [ ] Dataset explorer shows schema, sample data, and column stats
- [ ] Landing page updated with accurate Nemo messaging
- [ ] Responsive layout for all views
- [ ] Error handling covers LLM failures, execution timeouts, and connection drops
- [ ] SSE auto-reconnect with history replay
- [ ] Keyboard shortcuts updated for Nemo actions

---

## Data Model Summary

```
┌───────────────────────────────────────────────────────────────┐
│ datasets                                                       │
├───────────────────────────────────────────────────────────────┤
│ id               TEXT PRIMARY KEY                              │
│ name             TEXT NOT NULL                                 │
│ description      TEXT                                          │
│ source_type      TEXT ('csv' | 'postgres' | 'sqlite')         │
│ connection_info  TEXT (file path or connection string)         │
│ profile          TEXT (JSON: DatasetProfile)                   │
│ row_count        INTEGER                                       │
│ column_count     INTEGER                                       │
│ created_at       TEXT                                           │
└───────────────────────────────────────────────────────────────┘
         │
         │ 1:N
         ▼
┌───────────────────────────────────────────────────────────────┐
│ sessions                                                       │
├───────────────────────────────────────────────────────────────┤
│ id               TEXT PRIMARY KEY                              │
│ dataset_id       TEXT → datasets(id)                           │
│ hypothesis       TEXT NOT NULL                                 │
│ context          TEXT                                           │
│ status           TEXT (idle|diving|reflecting|surfaced|paused) │
│ config           TEXT (JSON: SessionConfig)                    │
│ created_at       TEXT                                           │
│ updated_at       TEXT                                           │
└───────────────────────────────────────────────────────────────┘
         │
         │ 1:N                          1:N
         ▼                              ▼
┌─────────────────────────────┐  ┌─────────────────────────────┐
│ nodes                        │  │ events                       │
├─────────────────────────────┤  ├─────────────────────────────┤
│ id          TEXT PK          │  │ id          INTEGER PK AUTO  │
│ session_id  TEXT → sessions  │  │ session_id  TEXT → sessions  │
│ type        TEXT             │  │ type        TEXT             │
│ status      TEXT             │  │ payload     TEXT (JSON)      │
│ question    TEXT             │  │ created_at  TEXT             │
│ code        TEXT             │  └─────────────────────────────┘
│ result      TEXT (JSON)      │
│ answer      TEXT             │
│ confidence  REAL             │
│ summary     TEXT             │
│ supported_by TEXT (JSON)     │
│ depth       INTEGER          │
│ priority    REAL             │
│ created_at  TEXT             │
└─────────────────────────────┘
         │
         │ N:N (via edges)
         ▼
┌─────────────────────────────┐
│ edges                        │
├─────────────────────────────┤
│ id          TEXT PK          │
│ session_id  TEXT → sessions  │
│ source_id   TEXT → nodes     │
│ target_id   TEXT → nodes     │
│ type        TEXT             │
│ reasoning   TEXT             │
│ created_at  TEXT             │
└─────────────────────────────┘
```

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                      Nuxt 3 Frontend                          │
│                                                               │
│  pages/sessions/[id].vue                                      │
│    ├── ExplorationGraph.vue  (D3 force-directed)              │
│    ├── ActivityFeed.vue      (streaming log)                  │
│    └── NodeDetail.vue        (tabs: question/code/result)     │
│                                                               │
│  useSession(id) composable                                    │
│    ← EventSource(/api/sessions/:id/stream)                    │
│    → reactive: nodes, edges, status, feed                     │
│    → computed: exploringNode, frontier, syntheses              │
│    → actions: dive(), pause(), resume()                       │
└──────────────────────────────────────────────────────────────┘
                            │
                   SSE + REST API
                            │
┌──────────────────────────────────────────────────────────────┐
│                      Nitro Server                             │
│                                                               │
│  API Routes                                                   │
│    /api/sessions/*    → CRUD + controls                       │
│    /api/datasets/*    → upload + profile                      │
│                                                               │
│  SessionManager (singleton)                                   │
│    → manages engine instances per session                     │
│    → wires engine events to SSE subscribers                   │
│    → persists events to Supabase for replay                   │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Engine (pure, zero Nuxt imports)                       │  │
│  │                                                         │  │
│  │  run(emit) → the outer loop                             │  │
│  │    Select frontier → Generate question → Write code     │  │
│  │    → Execute → Interpret → Classify edges               │  │
│  │    → Suggest next → Reflect (periodic)                  │  │
│  │                                                         │  │
│  │  ┌─────────────┐ ┌──────────────┐ ┌────────────────┐  │  │
│  │  │  LLM Client │ │ Code Executor│ │  Graph Store   │  │  │
│  │  │  (Claude)   │ │ (Python sub- │ │  (Supabase +   │  │  │
│  │  │             │ │  process)    │ │   frontier)    │  │  │
│  │  └─────────────┘ └──────────────┘ └────────────────┘  │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                               │
│  Supabase Postgres + Storage                                  │
│    datasets · sessions · nodes · edges · events               │
└──────────────────────────────────────────────────────────────┘
```

---

## Testing Checklist

### Engine (Sprint 2)
- [ ] Engine runs standalone with `engine.run(console.log)` and produces valid events
- [ ] LLM client generates reasonable questions given a dataset profile
- [ ] Code executor runs Python and returns structured table/chart/scalar results
- [ ] Code executor enforces timeout on long-running scripts
- [ ] Frontier selection picks highest-priority node
- [ ] Reflection triggers at configured interval
- [ ] Engine stops at max_nodes limit

### Streaming (Sprint 3)
- [ ] SSE endpoint replays all past events on connect
- [ ] SSE endpoint streams live events during active session
- [ ] Reconnecting client receives full history + catches up
- [ ] Pause/resume preserves engine state correctly
- [ ] Events persist to Supabase and survive server restart

### Frontend (Sprint 4)
- [ ] Graph renders nodes at correct positions with correct colors
- [ ] New nodes animate in smoothly (no full graph re-layout)
- [ ] Clicking a node opens detail panel with correct data
- [ ] Activity feed auto-scrolls and respects scroll-lock
- [ ] Plotly charts render interactively from JSON specs
- [ ] Data tables sort correctly
- [ ] Session controls update engine state and UI reflects changes

### End-to-End (Sprint 5)
- [ ] Upload CSV → profile generated → schema visible
- [ ] Create session → provide hypothesis → dive starts
- [ ] Graph grows in real time as engine explores
- [ ] Pause mid-session → resume → exploration continues
- [ ] Engine surfaces → summary view shows synthesis
- [ ] Refresh page mid-session → SSE replays → state reconstructed

---

## Dependencies to Add

| Package | Purpose | Sprint |
|---------|---------|--------|
| `@supabase/supabase-js` | Supabase client for Postgres + Storage | 1 |
| `csv-parse` | CSV file parsing | 1 |
| `multer` or `formidable` | File upload handling | 1 |
| `@anthropic-ai/sdk` | Claude API client | 2 |
| `d3` | Force-directed graph visualization | 4 |
| `@types/d3` | TypeScript types | 4 |
| `plotly.js-dist-min` | Chart rendering in node results | 4 |
| `shiki` | Syntax highlighting for generated code | 4 |

---

## Environment Variables

```env
# LLM
ANTHROPIC_API_KEY=sk-ant-...
NEMO_DEFAULT_MODEL=claude-sonnet-4-20250514

# Supabase
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>

# Execution
NEMO_PYTHON_PATH=/usr/bin/python3
NEMO_EXEC_TIMEOUT=30000
NEMO_DATA_DIR=./data

# Engine defaults
NEMO_MAX_NODES=50
NEMO_REFLECT_EVERY=5
```

---

## File Checklist

| File | Sprint | Status | Purpose |
|------|--------|--------|---------|
| `server/core/types.ts` | 1 | ⬜ | All shared TypeScript types |
| `db_migrations/20260224_001_initial_nemo_schema.sql` | 1 | [✅ Completed] | Supabase/Postgres schema migration |
| `server/services/supabase.ts` | 1 | ⬜ | Supabase server client + data access helpers |
| `server/api/datasets/index.post.ts` | 1 | ⬜ | Upload dataset |
| `server/api/datasets/index.get.ts` | 1 | ⬜ | List datasets |
| `server/api/datasets/[id]/profile.get.ts` | 1 | ⬜ | Get dataset profile |
| `app/types/index.d.ts` | 1 | ⬜ | Frontend type re-exports |
| `app/pages/sessions.vue` | 1 (stub) | ⬜ | Sessions list |
| `app/pages/sessions/new.vue` | 1 (stub) | ⬜ | New session setup |
| `app/pages/sessions/[id].vue` | 1 (stub) | ⬜ | Session view |
| `app/pages/datasets.vue` | 1 (stub) | ⬜ | Dataset library |
| `server/core/llm.ts` | 2 | ⬜ | Claude API wrapper |
| `server/core/prompts.ts` | 2 | ⬜ | LLM prompt templates |
| `server/core/executor.ts` | 2 | ⬜ | Python execution sandbox |
| `server/core/executor_wrapper.py` | 2 | ⬜ | Python harness script |
| `server/core/graph.ts` | 2 | ⬜ | Graph store (nodes, edges, frontier) |
| `server/core/frontier.ts` | 2 | ⬜ | Priority scoring |
| `server/core/engine.ts` | 2 | ⬜ | The outer exploration loop |
| `server/api/sessions/index.post.ts` | 3 | ⬜ | Create session |
| `server/api/sessions/index.get.ts` | 3 | ⬜ | List sessions |
| `server/api/sessions/[id].get.ts` | 3 | ⬜ | Get session + graph |
| `server/api/sessions/[id]/dive.post.ts` | 3 | ⬜ | Start exploration |
| `server/api/sessions/[id]/pause.post.ts` | 3 | ⬜ | Pause engine |
| `server/api/sessions/[id]/resume.post.ts` | 3 | ⬜ | Resume engine |
| `server/api/sessions/[id]/stream.get.ts` | 3 | ⬜ | SSE event stream |
| `server/services/session.ts` | 3 | ⬜ | Session manager (engine ↔ SSE glue) |
| `app/composables/useSession.ts` | 3 | ⬜ | Reactive SSE consumer |
| `app/pages/sessions/[id].vue` | 4 | ⬜ | Session view (full implementation) |
| `app/components/graph/ExplorationGraph.vue` | 4 | ⬜ | D3 force-directed graph |
| `app/components/graph/GraphNode.vue` | 4 | ⬜ | Node rendering |
| `app/components/graph/GraphEdge.vue` | 4 | ⬜ | Edge rendering |
| `app/components/session/ActivityFeed.vue` | 4 | ⬜ | Streaming activity log |
| `app/components/session/NodeDetail.vue` | 4 | ⬜ | Node detail panel with tabs |
| `app/components/session/NodeResult.vue` | 4 | ⬜ | Table/chart/scalar renderer |
| `app/components/session/SessionControls.vue` | 4 | ⬜ | Pause/resume/stop bar |
| `app/components/session/SynthesisSummary.vue` | 4 | ⬜ | Synthesis display |
| `app/pages/sessions.vue` | 5 | ⬜ | Sessions list (full implementation) |
| `app/pages/sessions/new.vue` | 5 | ⬜ | New session setup (full implementation) |
| `app/components/session/SummaryView.vue` | 5 | ⬜ | Post-dive summary |
| `app/pages/datasets.vue` | 5 | ⬜ | Dataset explorer (full implementation) |
| `app/components/dataset/DatasetUpload.vue` | 5 | ⬜ | Upload + profile component |
| `app/components/dataset/SchemaViewer.vue` | 5 | ⬜ | Column browser |
| `app/pages/index.vue` | 5 | ⬜ | Updated landing page |
