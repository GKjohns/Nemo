import { getSupabase } from '~~/server/services/supabase'
import { sessionManager } from '~~/server/services/session'

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id')
  if (!id) {
    throw createError({ statusCode: 400, statusMessage: 'Session id is required.' })
  }

  const supabase = await getSupabase(event)

  try {
    await sessionManager.stopSession(id, supabase)
  } catch (error) {
    throw createError({
      statusCode: 400,
      statusMessage: error instanceof Error ? error.message : 'Failed to stop session.'
    })
  }

  return { ok: true, session_id: id, status: 'surfaced' }
})
