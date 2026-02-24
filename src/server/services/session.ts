import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, join } from 'node:path'
import type { SupabaseClient } from '@supabase/supabase-js'
import type { Database } from '~~/app/types/database.types'
import { ChartRenderer } from '~~/server/core/chartRenderer'
import { NemoEngine } from '~~/server/core/engine'
import { DuckDbExecutor } from '~~/server/core/executor'
import { GraphStore } from '~~/server/core/graph'
import { LLMClient } from '~~/server/core/llm'
import type { NemoEvent, SessionConfig } from '~~/server/core/types'
import { getSupabaseServiceRole } from '~~/server/services/supabase'

type DbClient = SupabaseClient<Database>

type SessionRow = Database['public']['Tables']['sessions']['Row']
type DatasetRow = Database['public']['Tables']['datasets']['Row']
type EventRow = Database['public']['Tables']['events']['Row']

const DEFAULT_SESSION_CONFIG: SessionConfig = {
  max_nodes: 40,
  reflect_every: 5,
  model: 'gpt-5-mini'
}

interface Runtime {
  engine: NemoEngine
}

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error'
}

function toSessionConfig(value: unknown): SessionConfig {
  const input = (value && typeof value === 'object') ? value as Partial<SessionConfig> : {}

  return {
    max_nodes: typeof input.max_nodes === 'number' && Number.isFinite(input.max_nodes)
      ? Math.max(1, Math.floor(input.max_nodes))
      : DEFAULT_SESSION_CONFIG.max_nodes,
    reflect_every: typeof input.reflect_every === 'number' && Number.isFinite(input.reflect_every)
      ? Math.max(1, Math.floor(input.reflect_every))
      : DEFAULT_SESSION_CONFIG.reflect_every,
    model: typeof input.model === 'string' && input.model.trim().length > 0
      ? input.model.trim()
      : DEFAULT_SESSION_CONFIG.model
  }
}

class SessionManager {
  private engines = new Map<string, Runtime>()
  private subscribers = new Map<string, Set<(event: NemoEvent) => void>>()
  private eventChains = new Map<string, Promise<void>>()
  private localCsvCache = new Map<string, string>()
  private chartRenderer: ChartRenderer | null = null

  async startSession(sessionId: string, supabase: DbClient): Promise<void> {
    if (this.engines.has(sessionId)) {
      this.debug('start:already_running', { session_id: sessionId })
      throw new Error(`Session ${sessionId} is already running.`)
    }

    this.debug('start:building_runtime', { session_id: sessionId })
    const runtime = await this.buildRuntime(sessionId, supabase)
    this.engines.set(sessionId, runtime)
    this.debug('start:engine_started', { session_id: sessionId })

    const emit = (event: NemoEvent) => {
      this.debug('event:queued', { session_id: sessionId, type: event.type })
      const prev = this.eventChains.get(sessionId) ?? Promise.resolve()
      const next = prev
        .then(() => this.persistAndBroadcast(sessionId, event, supabase))
        .catch((error) => {
          this.debug('event:queue_failed', {
            session_id: sessionId,
            type: event.type,
            error: toErrorMessage(error)
          })
        })
      this.eventChains.set(sessionId, next)
    }

    void runtime.engine
      .run(emit)
      .catch((error) => {
        emit({
          type: 'session:error',
          error: `Engine failed: ${toErrorMessage(error)}`
        })
      })
      .finally(() => {
        this.engines.delete(sessionId)
        this.debug('start:engine_finished', { session_id: sessionId })
      })
  }

  async pauseSession(sessionId: string, supabase: DbClient): Promise<void> {
    const runtime = this.engines.get(sessionId)
    if (!runtime) {
      await this.persistAndBroadcast(sessionId, { type: 'session:status', status: 'paused' }, supabase)
      return
    }

    runtime.engine.pause()
    await this.persistAndBroadcast(sessionId, { type: 'session:status', status: 'paused' }, supabase)
  }

  async resumeSession(sessionId: string, supabase: DbClient): Promise<void> {
    await this.startSession(sessionId, supabase)
  }

  async stopSession(sessionId: string, supabase: DbClient): Promise<void> {
    const runtime = this.engines.get(sessionId)
    if (runtime) {
      runtime.engine.stop()
    }

    await this.persistAndBroadcast(sessionId, { type: 'session:status', status: 'surfaced' }, supabase)
  }

  subscribe(sessionId: string, callback: (event: NemoEvent) => void): () => void {
    const listeners = this.subscribers.get(sessionId) ?? new Set<(event: NemoEvent) => void>()
    listeners.add(callback)
    this.subscribers.set(sessionId, listeners)
    this.debug('stream:subscriber_added', { session_id: sessionId, count: listeners.size })

    return () => {
      const current = this.subscribers.get(sessionId)
      if (!current) return
      current.delete(callback)
      this.debug('stream:subscriber_removed', { session_id: sessionId, count: current.size })
      if (current.size === 0) {
        this.subscribers.delete(sessionId)
      }
    }
  }

  async getEvents(sessionId: string, supabase: DbClient): Promise<NemoEvent[]> {
    const { data, error } = await supabase
      .from('events')
      .select('payload')
      .eq('session_id', sessionId)
      .order('id', { ascending: true })

    if (error) {
      throw new Error(`Failed to load events: ${error.message}`)
    }

    return (data ?? []).map(row => row.payload as unknown as NemoEvent)
  }

  private async persistAndBroadcast(sessionId: string, event: NemoEvent, supabase: DbClient): Promise<void> {
    const startedAt = Date.now()
    const { error } = await supabase
      .from('events')
      .insert({
        session_id: sessionId,
        type: event.type,
        payload: event as unknown as EventRow['payload']
      })

    if (error) {
      throw new Error(`Failed to append event: ${error.message}`)
    }

    if (event.type === 'session:status') {
      const { error: updateError } = await supabase
        .from('sessions')
        .update({
          status: event.status,
          updated_at: new Date().toISOString()
        })
        .eq('id', sessionId)

      if (updateError) {
        throw new Error(`Failed to update session status: ${updateError.message}`)
      }
    }

    this.subscribers.get(sessionId)?.forEach(callback => callback(event))
    this.debug('event:broadcasted', {
      session_id: sessionId,
      type: event.type,
      subscriber_count: this.subscribers.get(sessionId)?.size ?? 0,
      elapsed_ms: Date.now() - startedAt
    })
  }

  private async buildRuntime(sessionId: string, supabase: DbClient): Promise<Runtime> {
    const session = await this.getSessionRow(sessionId, supabase)
    const dataset = await this.getDatasetRow(session.dataset_id, supabase)
    this.debug('runtime:loaded_rows', {
      session_id: sessionId,
      dataset_id: session.dataset_id,
      session_status: session.status
    })

    if (!dataset.profile) {
      throw new Error('Dataset profile is missing; re-profile the dataset before diving.')
    }

    const apiKey = process.env.OPENAI_API_KEY
    if (!apiKey) {
      throw new Error('Missing OPENAI_API_KEY.')
    }

    const config = toSessionConfig(session.config)
    const graph = new GraphStore(session.id, session.hypothesis, supabase)
    await graph.load()
    this.debug('runtime:graph_loaded', {
      session_id: session.id,
      node_count: graph.nodeCount(),
      max_nodes: config.max_nodes,
      reflect_every: config.reflect_every
    })

    const csvPath = await this.getLocalCsvPath(dataset.connection_info)

    const engine = new NemoEngine(
      graph,
      dataset.profile,
      csvPath,
      new LLMClient(apiKey, config.model),
      new DuckDbExecutor(),
      this.getChartRenderer(),
      config
    )

    return { engine }
  }

  private getChartRenderer(): ChartRenderer {
    if (this.chartRenderer) {
      return this.chartRenderer
    }

    try {
      this.chartRenderer = new ChartRenderer()
    } catch {
      this.chartRenderer = {
        async render() {
          throw new Error('ChartRenderer is disabled due to missing storage credentials.')
        }
      } as unknown as ChartRenderer
    }

    return this.chartRenderer
  }

  private async getLocalCsvPath(storagePath: string): Promise<string> {
    const cached = this.localCsvCache.get(storagePath)
    if (cached) {
      this.debug('dataset:csv_cache_hit', { storage_path: storagePath })
      return cached
    }

    const storageClient = getSupabaseServiceRole()
    const { data, error } = await storageClient.storage.from('datasets').download(storagePath)
    if (error || !data) {
      const detail = error?.message ?? JSON.stringify(error) ?? 'no file returned'
      throw new Error(`Failed to download dataset CSV at "${storagePath}": ${detail}`)
    }

    const bytes = Buffer.from(await data.arrayBuffer())
    const tempDir = await mkdtemp(join(tmpdir(), 'nemo-'))
    const filePath = join(tempDir, basename(storagePath) || 'dataset.csv')
    await writeFile(filePath, bytes)
    this.localCsvCache.set(storagePath, filePath)
    this.debug('dataset:csv_cached', { storage_path: storagePath, local_path: filePath })
    return filePath
  }

  private async getSessionRow(sessionId: string, supabase: DbClient): Promise<SessionRow> {
    const { data, error } = await supabase
      .from('sessions')
      .select('*')
      .eq('id', sessionId)
      .single()

    if (error || !data) {
      throw new Error(`Session not found: ${sessionId}`)
    }

    return data as SessionRow
  }

  private async getDatasetRow(datasetId: string, supabase: DbClient): Promise<DatasetRow> {
    const { data, error } = await supabase
      .from('datasets')
      .select('*')
      .eq('id', datasetId)
      .single()

    if (error || !data) {
      throw new Error(`Dataset not found: ${datasetId}`)
    }

    return data as DatasetRow
  }

  private debug(message: string, details?: Record<string, unknown>): void {
    if (details) {
      console.log(`[nemo:session] ${message}`, details)
      return
    }
    console.log(`[nemo:session] ${message}`)
  }
}

declare global {
  var __nemoSessionManager: SessionManager | undefined
}

export const sessionManager = globalThis.__nemoSessionManager ?? new SessionManager()
if (!globalThis.__nemoSessionManager) {
  globalThis.__nemoSessionManager = sessionManager
}

export { DEFAULT_SESSION_CONFIG, toSessionConfig }
