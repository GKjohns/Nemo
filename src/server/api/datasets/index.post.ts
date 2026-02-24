import { parse } from 'csv-parse/sync'
import { buildDatasetProfile } from '~~/server/core/datasetProfile'
import type { DatasetInsert } from '~~/server/services/supabase'
import { getSupabase } from '~~/server/services/supabase'

function sanitizeName(fileName: string): string {
  return fileName
    .replace(/[^a-zA-Z0-9.-]/g, '_')
    .replace(/_+/g, '_')
}

export default defineEventHandler(async (event) => {
  const formData = await readMultipartFormData(event)
  if (!formData || formData.length === 0) {
    throw createError({ statusCode: 400, statusMessage: 'Expected multipart form data.' })
  }

  const file = formData.find(part => part.name === 'file')
  if (!file?.data || !file.filename) {
    throw createError({ statusCode: 400, statusMessage: 'A CSV file is required in `file`.' })
  }

  if (!file.filename.toLowerCase().endsWith('.csv')) {
    throw createError({ statusCode: 400, statusMessage: 'Only CSV uploads are supported.' })
  }

  const metadataName = formData.find(part => part.name === 'name')?.data?.toString('utf8').trim()
  const metadataDescription = formData.find(part => part.name === 'description')?.data?.toString('utf8').trim()
  const datasetName = metadataName || file.filename.replace(/\.csv$/i, '')

  const records = parse(file.data.toString('utf8'), {
    columns: true,
    skip_empty_lines: true,
    relax_column_count: true,
    trim: true
  }) as Record<string, unknown>[]

  const profile = buildDatasetProfile(records)
  const datasetId = crypto.randomUUID()
  const storagePath = `${datasetId}/${sanitizeName(file.filename)}`

  const supabase = await getSupabase(event)
  const storageUpload = await supabase.storage.from('datasets').upload(storagePath, file.data, {
    contentType: file.type ?? 'text/csv',
    upsert: false
  })

  if (storageUpload.error) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to upload CSV to storage: ${storageUpload.error.message}`
    })
  }

  const datasetInsert: DatasetInsert = {
    id: datasetId,
    name: datasetName,
    description: metadataDescription || null,
    source_type: 'csv',
    connection_info: storagePath,
    profile,
    row_count: profile.row_count,
    column_count: profile.columns.length
  }

  const { data, error } = await supabase
    .from('datasets')
    .insert(datasetInsert)
    .select('id, name, description, source_type, row_count, column_count, created_at')
    .single()

  if (error || !data) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to persist dataset metadata: ${error?.message ?? 'unknown error'}`
    })
  }

  return {
    dataset: data
  }
})
