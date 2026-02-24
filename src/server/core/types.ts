export interface DatasetProfile {
  columns: ColumnProfile[]
  row_count: number
  relationships: DetectedRelationship[]
}

export interface ColumnProfile {
  name: string
  dtype: string
  sample_values: unknown[]
  nulls: number
  distribution_summary: string
}

export interface DetectedRelationship {
  from_column: string
  to_column: string
  type: string
}

export type SessionStatus = 'idle' | 'diving' | 'reflecting' | 'surfaced' | 'paused'

export interface SessionConfig {
  max_nodes: number
  reflect_every: number
  model: string
}

export type NodeType = 'hypothesis' | 'insight' | 'synthesis'
export type NodeStatus = 'frontier' | 'exploring' | 'complete' | 'dead_end'

export interface NodeResult {
  type: 'table' | 'chart' | 'scalar' | 'error'
  data: unknown
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
  summary: string | null
  supported_by: string[] | null
  depth: number
  priority: number
  created_at: string
}

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

export type NemoEvent =
  | { type: 'node:created'; node: Node }
  | { type: 'node:updated'; node: Node }
  | { type: 'edge:created'; edge: Edge }
  | { type: 'session:status'; status: SessionStatus }
  | { type: 'session:error'; error: string }

export interface FeedItem {
  id: string
  timestamp: string
  event_type: NemoEvent['type']
  title: string
  detail: string | null
  node_id: string | null
}
