import type { NemoEvent } from '~~/server/core/types'
import { sessionManager } from '~~/server/services/session'
import { getSupabase } from '~~/server/services/supabase'

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error'
}

function debug(message: string, details?: Record<string, unknown>): void {
  if (details) {
    console.log(`[nemo:stream] ${message}`, details)
    return
  }
  console.log(`[nemo:stream] ${message}`)
}

export default defineEventHandler(async (event) => {
  const id = getRouterParam(event, 'id')
  if (!id) {
    throw createError({ statusCode: 400, statusMessage: 'Session id is required.' })
  }

  const supabase = await getSupabase(event)
  const stream = createEventStream(event)
  debug('connect', { session_id: id })

  const unsubscribe = sessionManager.subscribe(id, (nemoEvent: NemoEvent) => {
    debug('event:push', { session_id: id, type: nemoEvent.type })
    void stream.push(JSON.stringify(nemoEvent)).catch((error) => {
      debug('event:push_failed', { session_id: id, error: toErrorMessage(error) })
    })
  })

  stream.onClosed(() => {
    debug('disconnect', { session_id: id })
    unsubscribe()
  })

  sessionManager.getEvents(id, supabase)
    .then((history) => {
      debug('history:loaded', { session_id: id, count: history.length })
      for (const payload of history) {
        void stream.push(JSON.stringify(payload))
      }
      debug('history:sent', { session_id: id, count: history.length })
    })
    .catch((error) => {
      debug('history:failed', { session_id: id, error: toErrorMessage(error) })
    })

  try {
    return stream.send()
  } catch (error) {
    unsubscribe()
    debug('send:failed', { session_id: id, error: toErrorMessage(error) })
    throw createError({
      statusCode: 500,
      statusMessage: `Failed to open stream: ${toErrorMessage(error)}`
    })
  }
})
