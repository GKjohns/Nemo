import { randomUUID } from 'node:crypto'
import type { SupabaseClient } from '@supabase/supabase-js'
import type { Node, Edge, GraphContext } from './types'

export class GraphStore {
  private nodes = new Map<string, Node>()
  private edgeList: Edge[] = []

  constructor(
    private readonly sessionId: string,
    private readonly hypothesis: string,
    private readonly supabase?: SupabaseClient
  ) {}

  getSessionId(): string {
    return this.sessionId
  }

  /**
   * Hydrate in-memory state from Supabase. Call once when resuming a session.
   * No-op when running without Supabase (purely in-memory).
   */
  async load(): Promise<void> {
    if (!this.supabase) return

    const { data: nodeRows, error: nodeErr } = await this.supabase
      .from('nodes')
      .select('*')
      .eq('session_id', this.sessionId)
      .order('created_at', { ascending: true })

    if (nodeErr) throw new Error(`Failed to load nodes: ${nodeErr.message}`)

    for (const row of nodeRows ?? []) {
      this.nodes.set(row.id, this.toNode(row))
    }

    const { data: edgeRows, error: edgeErr } = await this.supabase
      .from('edges')
      .select('*')
      .eq('session_id', this.sessionId)
      .order('created_at', { ascending: true })

    if (edgeErr) throw new Error(`Failed to load edges: ${edgeErr.message}`)

    this.edgeList = (edgeRows ?? []).map(row => this.toEdge(row))
  }

  // --------------- Node operations ---------------

  async createNode(data: Omit<Node, 'id' | 'created_at'>): Promise<Node> {
    if (this.supabase) {
      const { data: row, error } = await this.supabase
        .from('nodes')
        .insert({
          session_id: data.session_id,
          type: data.type,
          status: data.status,
          question: data.question,
          code: data.code,
          result: data.result as Record<string, unknown> | null,
          answer: data.answer,
          confidence: data.confidence,
          viz_spec: data.viz_spec as Record<string, unknown> | null,
          chart_image_url: data.chart_image_url,
          summary: data.summary,
          supported_by: data.supported_by,
          depth: data.depth,
          priority: data.priority
        })
        .select()
        .single()

      if (error || !row) {
        throw new Error(`Failed to create node: ${error?.message ?? 'no data returned'}`)
      }

      const node = this.toNode(row)
      this.nodes.set(node.id, node)
      return node
    }

    const node: Node = {
      ...data,
      id: randomUUID(),
      created_at: new Date().toISOString()
    }
    this.nodes.set(node.id, node)
    return node
  }

  async updateNode(id: string, updates: Partial<Node>): Promise<Node> {
    const existing = this.nodes.get(id)
    if (!existing) throw new Error(`Node ${id} not found`)

    const updated: Node = { ...existing, ...updates, id: existing.id, created_at: existing.created_at }

    if (this.supabase) {
      const { error } = await this.supabase
        .from('nodes')
        .update({
          status: updated.status,
          question: updated.question,
          code: updated.code,
          result: updated.result as Record<string, unknown> | null,
          answer: updated.answer,
          confidence: updated.confidence,
          viz_spec: updated.viz_spec as Record<string, unknown> | null,
          chart_image_url: updated.chart_image_url,
          summary: updated.summary,
          supported_by: updated.supported_by,
          depth: updated.depth,
          priority: updated.priority
        })
        .eq('id', id)

      if (error) throw new Error(`Failed to update node: ${error.message}`)
    }

    this.nodes.set(id, updated)
    return updated
  }

  async getNode(id: string): Promise<Node | null> {
    return this.nodes.get(id) ?? null
  }

  async getNodes(): Promise<Node[]> {
    return Array.from(this.nodes.values())
  }

  // --------------- Edge operations ---------------

  async createEdge(data: Omit<Edge, 'id' | 'created_at'>): Promise<Edge> {
    if (this.supabase) {
      const { data: row, error } = await this.supabase
        .from('edges')
        .insert({
          session_id: data.session_id,
          source_id: data.source_id,
          target_id: data.target_id,
          type: data.type,
          reasoning: data.reasoning
        })
        .select()
        .single()

      if (error || !row) {
        throw new Error(`Failed to create edge: ${error?.message ?? 'no data returned'}`)
      }

      const edge = this.toEdge(row)
      this.edgeList.push(edge)
      return edge
    }

    const edge: Edge = {
      ...data,
      id: randomUUID(),
      created_at: new Date().toISOString()
    }
    this.edgeList.push(edge)
    return edge
  }

  async getEdges(): Promise<Edge[]> {
    return [...this.edgeList]
  }

  async getEdgesForNode(nodeId: string): Promise<Edge[]> {
    return this.edgeList.filter(e => e.source_id === nodeId || e.target_id === nodeId)
  }

  // --------------- Frontier ---------------

  async selectFrontier(): Promise<Node | null> {
    let best: Node | null = null
    for (const node of this.nodes.values()) {
      if (node.status !== 'frontier') continue
      if (!best || node.priority > best.priority) {
        best = node
      }
    }
    return best
  }

  // --------------- Context ---------------

  async getGraphContext(): Promise<GraphContext> {
    const nodes = Array.from(this.nodes.values())
    return {
      hypothesis: this.hypothesis,
      nodes,
      edges: [...this.edgeList],
      stats: {
        node_count: nodes.length,
        edge_count: this.edgeList.length,
        max_depth: this.maxDepth(),
        frontier_count: nodes.filter(n => n.status === 'frontier').length
      }
    }
  }

  // --------------- Stats ---------------

  nodeCount(): number {
    return this.nodes.size
  }

  maxDepth(): number {
    let max = 0
    for (const node of this.nodes.values()) {
      if (node.depth > max) max = node.depth
    }
    return max
  }

  // --------------- Row mappers ---------------

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private toNode(row: Record<string, any>): Node {
    return {
      id: row.id,
      session_id: row.session_id,
      type: row.type,
      status: row.status,
      question: row.question ?? null,
      code: row.code ?? null,
      result: row.result ?? null,
      answer: row.answer ?? null,
      confidence: row.confidence ?? null,
      viz_spec: row.viz_spec ?? null,
      chart_image_url: row.chart_image_url ?? null,
      summary: row.summary ?? null,
      supported_by: row.supported_by ?? null,
      depth: row.depth ?? 0,
      priority: row.priority ?? 0,
      created_at: row.created_at
    }
  }

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private toEdge(row: Record<string, any>): Edge {
    return {
      id: row.id,
      session_id: row.session_id,
      source_id: row.source_id,
      target_id: row.target_id,
      type: row.type,
      reasoning: row.reasoning ?? null,
      created_at: row.created_at
    }
  }
}
