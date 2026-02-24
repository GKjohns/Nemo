import type {
  GraphContext,
  NemoEvent,
  Node,
  SessionConfig,
  SessionStatus,
  DatasetProfile,
  VizSpec
} from './types'
import type { LLMClient } from './llm'
import type { DuckDbExecutor } from './executor'
import type { ChartRenderer } from './chartRenderer'
import type { GraphStore } from './graph'

export class NemoEngine {
  private status: SessionStatus = 'idle'

  constructor(
    private graph: GraphStore,
    private dataset: DatasetProfile,
    private csvPath: string,
    private llm: LLMClient,
    private executor: DuckDbExecutor,
    private chartRenderer: ChartRenderer,
    private config: SessionConfig
  ) {}

  async run(emit: (event: NemoEvent) => void): Promise<void> {
    const startedAt = Date.now()
    this.debug('run:start', {
      model: this.config.model,
      max_nodes: this.config.max_nodes,
      reflect_every: this.config.reflect_every
    })
    this.status = 'diving'
    emit({ type: 'session:status', status: 'diving' })

    await this.seedFrontierIfNeeded(emit)

    let iterations = 0

    while (this.status === 'diving') {
      const next = await this.graph.selectFrontier()
      if (!next) {
        this.debug('run:break:no_frontier', { iterations, node_count: this.graph.nodeCount() })
        break
      }

      this.debug('run:loop:frontier', {
        iterations,
        node_id: next.id,
        node_status: next.status,
        priority: next.priority,
        depth: next.depth
      })

      try {
        await this.exploreNode(next, emit)
      } catch (error) {
        this.debug('run:loop:explore_failed', {
          node_id: next.id,
          error: error instanceof Error ? error.message : 'Unknown error'
        })
        await this.markDeadEnd(next, error, emit)
      }

      iterations++

      if (iterations > 0 && iterations % this.config.reflect_every === 0) {
        this.debug('run:loop:reflect', { iterations, session_id: next.session_id })
        await this.reflect(next.session_id, emit)
      }

      if (this.graph.nodeCount() >= this.config.max_nodes) {
        this.debug('run:break:max_nodes', {
          node_count: this.graph.nodeCount(),
          max_nodes: this.config.max_nodes
        })
        break
      }
    }

    if (this.getStatus() !== 'paused') {
      await this.surface(emit, iterations)
    }

    this.debug('run:end', {
      status: this.getStatus(),
      iterations,
      elapsed_ms: Date.now() - startedAt,
      node_count: this.graph.nodeCount()
    })
  }

  pause(): void {
    if (this.status === 'diving' || this.status === 'reflecting') {
      this.status = 'paused'
    }
  }

  stop(): void {
    this.status = 'surfaced'
  }

  getStatus(): SessionStatus {
    return this.status
  }

  // ---------------------------------------------------------------
  // Frontier seeding: ensure there is at least one frontier node
  // ---------------------------------------------------------------

  private async seedFrontierIfNeeded(emit: (event: NemoEvent) => void): Promise<void> {
    const existing = await this.graph.selectFrontier()
    if (existing) {
      this.debug('seed:has_frontier', { node_id: existing.id })
      return
    }

    const allNodes = await this.graph.getNodes()
    this.debug('seed:no_frontier', {
      node_count: allNodes.length,
      statuses: allNodes.map(n => `${n.type}:${n.status}`)
    })

    const unexploredHypothesis = allNodes.find(
      n => n.type === 'hypothesis' && n.status !== 'complete' && n.status !== 'dead_end'
    )
    if (unexploredHypothesis) {
      const reset = await this.graph.updateNode(unexploredHypothesis.id, { status: 'frontier' })
      emit({ type: 'node:updated', node: reset })
      this.debug('seed:reset_hypothesis', { node_id: reset.id, old_status: unexploredHypothesis.status })
      return
    }

    const completedNodes = allNodes
      .filter(n => n.status === 'complete' && n.type !== 'synthesis')
      .sort((a, b) => b.created_at.localeCompare(a.created_at))

    const seedSource = completedNodes[0]
    if (!seedSource) {
      this.debug('seed:no_completed_nodes_to_seed_from')
      return
    }

    this.debug('seed:generating_follow_ups', { source_id: seedSource.id, source_question: seedSource.question?.slice(0, 100) })
    const context = await this.graph.getGraphContext()
    await this.suggestFollowUps(seedSource, context, emit)
  }

  // ---------------------------------------------------------------
  // Core exploration step: question → SQL → execute → interpret
  // ---------------------------------------------------------------

  private async exploreNode(node: Node, emit: (event: NemoEvent) => void): Promise<void> {
    const exploreStartedAt = Date.now()
    const exploring = await this.graph.updateNode(node.id, { status: 'exploring' })
    emit({ type: 'node:updated', node: exploring })

    const graphContext = await this.graph.getGraphContext()
    this.debug('explore:start', {
      node_id: node.id,
      frontier_count: graphContext.stats.frontier_count,
      node_count: graphContext.stats.node_count
    })

    const question = node.question ?? await this.llm.generateQuestion(node, graphContext, this.dataset)
    this.debug('explore:question', {
      node_id: node.id,
      reused: Boolean(node.question),
      question_preview: question.slice(0, 140)
    })

    const sqlStartedAt = Date.now()
    const sql = await this.llm.generateSQL(question, this.dataset)
    this.debug('explore:sql', {
      node_id: node.id,
      elapsed_ms: Date.now() - sqlStartedAt,
      sql_preview: sql.slice(0, 180)
    })

    const queryStartedAt = Date.now()
    const result = await this.executor.run(sql, this.csvPath)
    this.debug('explore:query_result', {
      node_id: node.id,
      elapsed_ms: Date.now() - queryStartedAt,
      result_type: result.type
    })

    const interpretStartedAt = Date.now()
    const interpretation = await this.llm.interpret(result, question)
    this.debug('explore:interpret', {
      node_id: node.id,
      elapsed_ms: Date.now() - interpretStartedAt,
      confidence: interpretation.confidence
    })

    // Chart rendering — viz_spec will come from the interpret flow in a future iteration
    const vizSpec: VizSpec | null = null
    let chartImageUrl: string | null = null
    if (vizSpec && result.type === 'table') {
      try {
        chartImageUrl = await this.chartRenderer.render(result, vizSpec)
      } catch { /* non-fatal: skip chart on render failure */ }
    }

    const isDeadEnd = result.type === 'error'
    const completed = await this.graph.updateNode(node.id, {
      question,
      code: sql,
      result,
      answer: interpretation.answer,
      confidence: interpretation.confidence,
      viz_spec: vizSpec,
      chart_image_url: chartImageUrl,
      status: isDeadEnd ? 'dead_end' : 'complete'
    })
    emit({ type: 'node:updated', node: completed })

    const updatedContext = await this.graph.getGraphContext()
    await this.integrateEdges(completed, updatedContext, emit)
    await this.suggestFollowUps(completed, updatedContext, emit)

    this.debug('explore:end', {
      node_id: node.id,
      elapsed_ms: Date.now() - exploreStartedAt,
      status: completed.status
    })
  }

  // ---------------------------------------------------------------
  // Integration: classify edges to existing nodes
  // ---------------------------------------------------------------

  private async integrateEdges(
    node: Node,
    context: GraphContext,
    emit: (event: NemoEvent) => void
  ): Promise<void> {
    try {
      const edgeCandidates = await this.llm.classifyEdges(node, context)
      const existingIds = new Set(context.nodes.map(n => n.id))

      for (const data of edgeCandidates) {
        if (!existingIds.has(data.source_id) || !existingIds.has(data.target_id)) continue
        const edge = await this.graph.createEdge(data)
        emit({ type: 'edge:created', edge })
      }
    } catch (error) {
      emit({
        type: 'session:error',
        error: `Edge classification failed: ${error instanceof Error ? error.message : 'Unknown error'}`
      })
    }
  }

  // ---------------------------------------------------------------
  // Follow-ups: suggest new frontier nodes
  // ---------------------------------------------------------------

  private async suggestFollowUps(
    node: Node,
    context: GraphContext,
    emit: (event: NemoEvent) => void
  ): Promise<void> {
    try {
      const candidates = await this.llm.suggestNext(node, context, this.dataset)
      for (const data of candidates) {
        const newNode = await this.graph.createNode(data)
        emit({ type: 'node:created', node: newNode })
      }
    } catch (error) {
      emit({
        type: 'session:error',
        error: `Follow-up generation failed: ${error instanceof Error ? error.message : 'Unknown error'}`
      })
    }
  }

  // ---------------------------------------------------------------
  // Periodic reflection: synthesize findings mid-dive
  // ---------------------------------------------------------------

  private async reflect(sessionId: string, emit: (event: NemoEvent) => void): Promise<void> {
    try {
      const startedAt = Date.now()
      this.status = 'reflecting'
      emit({ type: 'session:status', status: 'reflecting' })

      await this.createSynthesis(sessionId, emit)

      this.status = 'diving'
      emit({ type: 'session:status', status: 'diving' })
      this.debug('reflect:success', { session_id: sessionId, elapsed_ms: Date.now() - startedAt })
    } catch (error) {
      emit({
        type: 'session:error',
        error: `Reflection failed: ${error instanceof Error ? error.message : 'Unknown error'}`
      })
      this.status = 'diving'
      this.debug('reflect:failed', {
        session_id: sessionId,
        error: error instanceof Error ? error.message : 'Unknown error'
      })
    }
  }

  // ---------------------------------------------------------------
  // Surfacing: final synthesis + status transition
  // ---------------------------------------------------------------

  private async surface(emit: (event: NemoEvent) => void, iterations: number): Promise<void> {
    const sessionId = this.graph.getSessionId()
    const allNodes = await this.graph.getNodes()
    const completedInsights = allNodes.filter(
      n => n.status === 'complete' && n.type !== 'synthesis'
    )

    if (completedInsights.length > 0 && iterations > 0) {
      this.debug('surface:reflecting', { completed_insights: completedInsights.length })
      this.status = 'reflecting'
      emit({ type: 'session:status', status: 'reflecting' })

      try {
        await this.createSynthesis(sessionId, emit)
      } catch (error) {
        this.debug('surface:synthesis_failed', {
          error: error instanceof Error ? error.message : 'Unknown error'
        })
        emit({
          type: 'session:error',
          error: `Final synthesis failed: ${error instanceof Error ? error.message : 'Unknown error'}`
        })
      }
    } else {
      this.debug('surface:skip_synthesis', {
        reason: completedInsights.length === 0 ? 'no completed insights' : 'no iterations run'
      })
    }

    this.status = 'surfaced'
    emit({ type: 'session:status', status: 'surfaced' })
    this.debug('surface:done', {
      session_id: sessionId,
      node_count: this.graph.nodeCount()
    })
  }

  // ---------------------------------------------------------------
  // Shared synthesis creation
  // ---------------------------------------------------------------

  private async createSynthesis(sessionId: string, emit: (event: NemoEvent) => void): Promise<void> {
    const graphContext = await this.graph.getGraphContext()
    const synthesis = await this.llm.synthesize(graphContext, graphContext.hypothesis)

    const synthNode = await this.graph.createNode({
      session_id: sessionId,
      type: 'synthesis',
      status: 'complete',
      summary: synthesis.summary,
      confidence: synthesis.confidence,
      supported_by: synthesis.supported_by,
      question: null,
      code: null,
      result: null,
      answer: null,
      viz_spec: null,
      chart_image_url: null,
      depth: 0,
      priority: 0
    })
    emit({ type: 'node:created', node: synthNode })
    this.debug('synthesis:created', {
      node_id: synthNode.id,
      confidence: synthesis.confidence,
      supported_by_count: synthesis.supported_by.length
    })
  }

  // ---------------------------------------------------------------
  // Error recovery: mark failed node as dead end
  // ---------------------------------------------------------------

  private async markDeadEnd(
    node: Node,
    error: unknown,
    emit: (event: NemoEvent) => void
  ): Promise<void> {
    try {
      const deadEnd = await this.graph.updateNode(node.id, {
        status: 'dead_end',
        result: {
          type: 'error',
          data: {
            message: error instanceof Error ? error.message : 'Unknown error during exploration',
            detail: null
          }
        }
      })
      emit({ type: 'node:updated', node: deadEnd })
      this.debug('dead_end:marked', { node_id: node.id })
    } catch {
      emit({
        type: 'session:error',
        error: `Failed to process node ${node.id}`
      })
      this.debug('dead_end:failed', { node_id: node.id })
    }
  }

  private debug(message: string, details?: Record<string, unknown>): void {
    if (details) {
      console.log(`[nemo:engine] ${message}`, details)
      return
    }
    console.log(`[nemo:engine] ${message}`)
  }
}
