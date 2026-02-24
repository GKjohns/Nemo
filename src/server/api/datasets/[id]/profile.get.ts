import type { DatasetProfile } from '~~/server/core/types'
import { getSupabase } from '~~/server/services/supabase'

export default defineEventHandler(async (event): Promise<{ profile: DatasetProfile }> => {
  const id = getRouterParam(event, 'id')
  if (!id) {
    throw createError({ statusCode: 400, statusMessage: 'Dataset id is required.' })
  }

  const supabase = await getSupabase(event)
  const { data, error } = await supabase
    .from('datasets')
    .select('profile')
    .eq('id', id)
    .single()

  if (error) {
    throw createError({
      statusCode: 404,
      statusMessage: `Dataset not found: ${error.message}`
    })
  }

  const profile = data?.profile as DatasetProfile | null
  if (!profile) {
    throw createError({
      statusCode: 404,
      statusMessage: 'Dataset profile not found.'
    })
  }

  return {
    profile
  }
})
