import type { Database } from '~~/app/types/database.types'
import { getSupabase, getSupabaseServiceRole } from '~~/server/services/supabase'

type DatasetRow = Database['public']['Tables']['datasets']['Row']

export default defineEventHandler(async (event): Promise<{ success: true }> => {
  const id = getRouterParam(event, 'id')
  if (!id) {
    throw createError({ statusCode: 400, statusMessage: 'Dataset id is required.' })
  }

  const supabase = await getSupabase(event)
  const { data, error } = await supabase
    .from('datasets')
    .delete()
    .eq('id', id)
    .select('connection_info, source_type')
    .single()

  if (error || !data) {
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to delete dataset: ${error?.message ?? 'unknown error'}`
    })
  }

  const row = data as Pick<DatasetRow, 'connection_info' | 'source_type'>
  if (row.source_type === 'csv' && row.connection_info) {
    const storageClient = getSupabaseServiceRole()
    const storageDelete = await storageClient.storage.from('datasets').remove([row.connection_info])
    if (storageDelete.error) {
      throw createError({
        statusCode: 500,
        statusMessage: `Dataset metadata deleted, but failed to remove CSV file: ${storageDelete.error.message}`
      })
    }
  }

  return { success: true }
})
