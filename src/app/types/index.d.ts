import type { SessionConfig, SessionStatus } from '~~/server/core/types'

export type {
  DatasetProfile,
  Edge,
  EdgeType,
  FeedItem,
  NemoEvent,
  Node,
  NodeResult,
  NodeStatus,
  NodeType,
  SessionConfig,
  SessionStatus
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
