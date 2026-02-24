import type { Session } from '~~/app/types'
import type { Database } from '~~/app/types/database.types'
import { GraphStore } from '~~/server/core/graph'
import type { SessionConfig } from '~~/server/core/types'
import { getSupabase } from '~~/server/services/supabase'
import { DEFAULT_SESSION_CONFIG, toSessionConfig } from '~~/server/services/session'

type SessionRow = Database['public']['Tables']['sessions']['Row']
type SessionInsert = Database['public']['Tables']['sessions']['Insert']

interface CreateSessionBody {
  dataset_id?: string
  hypothesis?: string
  context?: string | null
  config?: Partial<SessionConfig>
}

export default defineEventHandler(async (event): Promise<{ session: Session }> => {
  const body = await readBody<CreateSessionBody>(event)

  const datasetId = body.dataset_id?.trim()
  if (!datasetId) {
    throw createError({ statusCode: 400, statusMessage: '`dataset_id` is required.' })
  }

  const hypothesis = body.hypothesis?.trim()
  if (!hypothesis) {
    throw createError({ statusCode: 400, statusMessage: '`hypothesis` is required.' })
  }

  const supabase = await getSupabase(event)
  const { data: dataset, error: datasetError } = await supabase
    .from('datasets')
    .select('id')
    .eq('id', datasetId)
    .single()

  if (datasetError || !dataset) {
    throw createError({
      statusCode: 404,
      statusMessage: `Dataset not found: ${datasetId}`
    })
  }

  const config = toSessionConfig({ ...DEFAULT_SESSION_CONFIG, ...body.config })
  const { data: insertedSession, error } = await supabase
    .from('sessions')
    .insert({
      dataset_id: datasetId,
      hypothesis,
      context: body.context?.trim() || null,
      status: 'idle',
      config: config as unknown as SessionInsert['config']
    })
    .select('*')
    .single()

  if (error || !insertedSession) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to create session: ${error?.message ?? 'unknown error'}`
    })
  }

  const row = insertedSession as SessionRow

  const graph = new GraphStore(row.id, row.hypothesis, supabase)
  await graph.createNode({
    session_id: row.id,
    type: 'hypothesis',
    status: 'frontier',
    question: row.hypothesis,
    code: null,
    result: null,
    answer: null,
    confidence: null,
    viz_spec: null,
    chart_image_url: null,
    summary: null,
    supported_by: null,
    depth: 0,
    priority: 1
  })

  return {
    session: {
      id: row.id,
      dataset_id: row.dataset_id,
      hypothesis: row.hypothesis,
      context: row.context ?? null,
      status: row.status as Session['status'],
      config: toSessionConfig(row.config),
      node_count: 1,
      max_depth: 0,
      created_at: row.created_at,
      updated_at: row.updated_at
    }
  }
})
