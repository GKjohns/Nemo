import type { Dataset } from '~~/app/types'
import { getSupabase } from '~~/server/services/supabase'

export default defineEventHandler(async (event): Promise<{ datasets: Dataset[] }> => {
  const supabase = await getSupabase(event)
  const { data, error } = await supabase
    .from('datasets')
    .select('id, name, description, source_type, row_count, column_count, created_at')
    .order('created_at', { ascending: false })

  if (error) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to list datasets: ${error.message}`
    })
  }

  const datasets: Dataset[] = (data ?? []).map(row => ({
    id: row.id,
    name: row.name,
    description: row.description ?? null,
    source_type: row.source_type as Dataset['source_type'],
    row_count: row.row_count ?? null,
    column_count: row.column_count ?? null,
    created_at: row.created_at
  }))

  return { datasets }
})
