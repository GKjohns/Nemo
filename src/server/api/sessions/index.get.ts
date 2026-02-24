import type { Session } from '~~/app/types'
import type { Database } from '~~/app/types/database.types'
import { getSupabase } from '~~/server/services/supabase'
import { toSessionConfig } from '~~/server/services/session'

type SessionRow = Database['public']['Tables']['sessions']['Row']
type NodeStatRow = Pick<Database['public']['Tables']['nodes']['Row'], 'session_id' | 'depth'>

export default defineEventHandler(async (event): Promise<{ sessions: Session[] }> => {
  const supabase = await getSupabase(event)

  const { data: sessionRows, error: sessionError } = await supabase
    .from('sessions')
    .select('*')
    .order('updated_at', { ascending: false })

  if (sessionError) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to list sessions: ${sessionError.message}`
    })
  }

  const rows = (sessionRows ?? []) as SessionRow[]
  if (rows.length === 0) {
    return { sessions: [] }
  }

  const ids = rows.map(row => row.id)
  const { data: nodes, error: nodeError } = await supabase
    .from('nodes')
    .select('session_id, depth')
    .in('session_id', ids)

  if (nodeError) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to load session node stats: ${nodeError.message}`
    })
  }

  const stats = new Map<string, { node_count: number, max_depth: number }>()
  for (const node of (nodes ?? []) as NodeStatRow[]) {
    const current = stats.get(node.session_id) ?? { node_count: 0, max_depth: 0 }
    current.node_count += 1
    current.max_depth = Math.max(current.max_depth, node.depth ?? 0)
    stats.set(node.session_id, current)
  }

  return {
    sessions: rows.map((row) => {
      const summary = stats.get(row.id) ?? { node_count: 0, max_depth: 0 }
      return {
        id: row.id,
        dataset_id: row.dataset_id,
        hypothesis: row.hypothesis,
        context: row.context ?? null,
        status: row.status as Session['status'],
        config: toSessionConfig(row.config),
        node_count: summary.node_count,
        max_depth: summary.max_depth,
        created_at: row.created_at,
        updated_at: row.updated_at
      }
    })
  }
})
