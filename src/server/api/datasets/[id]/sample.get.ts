import { parse } from 'csv-parse/sync'
import type { Database } from '~~/app/types/database.types'
import { getSupabase, getSupabaseServiceRole } from '~~/server/services/supabase'

type DatasetRow = Database['public']['Tables']['datasets']['Row']
type CsvRow = Record<string, unknown>

function normalizeLimit(value: unknown): number {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 20
  return Math.max(1, Math.min(100, Math.floor(numeric)))
}

export default defineEventHandler(async (event): Promise<{ columns: string[], rows: CsvRow[] }> => {
  const id = getRouterParam(event, 'id')
  if (!id) {
    throw createError({ statusCode: 400, statusMessage: 'Dataset id is required.' })
  }

  const limit = normalizeLimit(getQuery(event).limit)
  const supabase = await getSupabase(event)

  const { data: dataset, error: datasetError } = await supabase
    .from('datasets')
    .select('source_type, connection_info')
    .eq('id', id)
    .single()

  if (datasetError || !dataset) {
    throw createError({
      statusCode: 404,
      statusMessage: `Dataset not found: ${id}`
    })
  }

  const row = dataset as Pick<DatasetRow, 'source_type' | 'connection_info'>
  if (row.source_type !== 'csv') {
    throw createError({
      statusCode: 400,
      statusMessage: 'Sample viewing is only supported for CSV datasets.'
    })
  }

  const storageClient = getSupabaseServiceRole()
  const download = await storageClient.storage.from('datasets').download(row.connection_info)
  if (download.error || !download.data) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to download CSV: ${download.error?.message ?? JSON.stringify(download.error) ?? 'empty response'}`
    })
  }

  const content = Buffer.from(await download.data.arrayBuffer()).toString('utf8')
  const rows = parse(content, {
    columns: true,
    skip_empty_lines: true,
    relax_column_count: true,
    trim: true
  }) as CsvRow[]

  const subset = rows.slice(0, limit)
  const first = subset[0]
  return {
    columns: first ? Object.keys(first) : [],
    rows: subset
  }
})
