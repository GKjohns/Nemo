import { getSupabase } from '~~/server/services/supabase'
import { sessionManager } from '~~/server/services/session'

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id')
  if (!id) {
    throw createError({ statusCode: 400, statusMessage: 'Session id is required.' })
  }

  const supabase = await getSupabase(event)

  try {
    await sessionManager.startSession(id, supabase)
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Failed to start session.'
    if (message.includes('already running')) {
      return { ok: true, session_id: id, status: 'diving' }
    }

    throw createError({
      statusCode: 400,
      statusMessage: message
    })
  }

  return { ok: true, session_id: id, status: 'diving' }
})
