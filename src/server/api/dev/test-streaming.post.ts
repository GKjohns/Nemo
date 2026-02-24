import type { NemoEvent } from '~~/server/core/types'
import { sessionManager } from '~~/server/services/session'
import { getSupabase } from '~~/server/services/supabase'

interface TestStreamingBody {
  session_id?: string
  timeout_ms?: number
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

export default defineEventHandler(async (event) => {
  const body = await readBody<TestStreamingBody>(event)
  const sessionId = body.session_id?.trim()
  if (!sessionId) {
    throw createError({ statusCode: 400, statusMessage: '`session_id` is required.' })
  }

  const timeoutMs = Math.min(Math.max(body.timeout_ms ?? 8000, 1000), 30000)
  const supabase = await getSupabase(event)
  const historyBefore = await sessionManager.getEvents(sessionId, supabase)
  const liveEvents: NemoEvent[] = []

  const unsubscribe = sessionManager.subscribe(sessionId, (nemoEvent) => {
    liveEvents.push(nemoEvent)
  })

  let startError: string | null = null

  try {
    try {
      await sessionManager.startSession(sessionId, supabase)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to start session.'
      if (!message.includes('already running')) {
        throw error
      }
      startError = message
    }

    const start = Date.now()
    while (liveEvents.length === 0 && Date.now() - start < timeoutMs) {
      await sleep(200)
    }

    if (liveEvents.length > 0) {
      await sessionManager.pauseSession(sessionId, supabase)
    }

    const historyAfter = await sessionManager.getEvents(sessionId, supabase)

    return {
      ok: liveEvents.length > 0,
      session_id: sessionId,
      timeout_ms: timeoutMs,
      start_error: startError,
      history_before: historyBefore.length,
      history_after: historyAfter.length,
      new_persisted_events: historyAfter.length - historyBefore.length,
      live_events_received: liveEvents.length,
      live_event_types: liveEvents.slice(0, 10).map(eventItem => eventItem.type)
    }
  } finally {
    unsubscribe()
  }
})
