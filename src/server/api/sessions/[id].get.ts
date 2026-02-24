import type { Session, Node, Edge } from '~~/app/types'
import type { Database } from '~~/app/types/database.types'
import { getSupabase } from '~~/server/services/supabase'
import { toSessionConfig } from '~~/server/services/session'

type SessionRow = Database['public']['Tables']['sessions']['Row']
type NodeRow = Database['public']['Tables']['nodes']['Row']
type EdgeRow = Database['public']['Tables']['edges']['Row']

export default defineEventHandler(async (event): Promise<{ session: Session, nodes: Node[], edges: Edge[] }> => {
  const id = getRouterParam(event, 'id')
  if (!id) {
    throw createError({ statusCode: 400, statusMessage: 'Session id is required.' })
  }

  const supabase = await getSupabase(event)
  const { data: sessionRow, error: sessionError } = await supabase
    .from('sessions')
    .select('*')
    .eq('id', id)
    .single()

  if (sessionError || !sessionRow) {
    throw createError({
      statusCode: 404,
      statusMessage: `Session not found: ${id}`
    })
  }

  const [{ data: nodeRows, error: nodeError }, { data: edgeRows, error: edgeError }] = await Promise.all([
    supabase
      .from('nodes')
      .select('*')
      .eq('session_id', id)
      .order('created_at', { ascending: true }),
    supabase
      .from('edges')
      .select('*')
      .eq('session_id', id)
      .order('created_at', { ascending: true })
  ])

  if (nodeError) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to load nodes: ${nodeError.message}`
    })
  }

  if (edgeError) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to load edges: ${edgeError.message}`
    })
  }

  const nodes = ((nodeRows ?? []) as NodeRow[]).map(row => ({
    id: row.id,
    session_id: row.session_id,
    type: row.type as Node['type'],
    status: row.status as Node['status'],
    question: row.question ?? null,
    code: row.code ?? null,
    result: row.result ?? null,
    answer: row.answer ?? null,
    confidence: row.confidence ?? null,
    viz_spec: row.viz_spec as Node['viz_spec'] ?? null,
    chart_image_url: row.chart_image_url ?? null,
    summary: row.summary ?? null,
    supported_by: row.supported_by ?? null,
    depth: row.depth,
    priority: row.priority,
    created_at: row.created_at
  }))

  const nodeCount = nodes.length
  const maxDepth = nodes.reduce((max, node) => Math.max(max, node.depth), 0)

  const edges = ((edgeRows ?? []) as EdgeRow[]).map(row => ({
    id: row.id,
    session_id: row.session_id,
    source_id: row.source_id,
    target_id: row.target_id,
    type: row.type as Edge['type'],
    reasoning: row.reasoning ?? null,
    created_at: row.created_at
  }))

  return {
    session: {
      id: (sessionRow as SessionRow).id,
      dataset_id: (sessionRow as SessionRow).dataset_id,
      hypothesis: (sessionRow as SessionRow).hypothesis,
      context: (sessionRow as SessionRow).context ?? null,
      status: (sessionRow as SessionRow).status as Session['status'],
      config: toSessionConfig((sessionRow as SessionRow).config),
      node_count: nodeCount,
      max_depth: maxDepth,
      created_at: (sessionRow as SessionRow).created_at,
      updated_at: (sessionRow as SessionRow).updated_at
    },
    nodes,
    edges
  }
})
