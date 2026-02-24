import { getSupabase } from '~~/server/services/supabase'

interface HealthCheck {
  name: string
  status: 'ok' | 'error'
  latency_ms?: number
  detail?: string
}

export default defineEventHandler(async (event) => {
  const checks: HealthCheck[] = []

  // --- Env vars ---
  const requiredEnvs = ['SUPABASE_URL', 'OPENAI_API_KEY']
  for (const key of requiredEnvs) {
    const value = process.env[key]
    checks.push({
      name: `env:${key}`,
      status: value ? 'ok' : 'error',
      detail: value ? `Set (${value.slice(0, 8)}…)` : 'Missing'
    })
  }

  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.SUPABASE_SERVICE_KEY ?? process.env.SUPABASE_KEY
  checks.push({
    name: 'env:SUPABASE_SERVICE_KEY',
    status: serviceKey ? 'ok' : 'error',
    detail: serviceKey ? `Set (${serviceKey.slice(0, 8)}…)` : 'Missing (checked SERVICE_ROLE_KEY, SERVICE_KEY, KEY)'
  })

  // --- Supabase connection ---
  try {
    const start = Date.now()
    const supabase = await getSupabase(event)
    const { error } = await supabase.from('datasets').select('id').limit(1)
    const latency = Date.now() - start
    checks.push({
      name: 'supabase:query',
      status: error ? 'error' : 'ok',
      latency_ms: latency,
      detail: error ? error.message : `Connected (${latency}ms)`
    })
  } catch (err) {
    checks.push({
      name: 'supabase:query',
      status: 'error',
      detail: err instanceof Error ? err.message : 'Connection failed'
    })
  }

  // --- Supabase Storage ---
  try {
    const start = Date.now()
    const supabase = await getSupabase(event)
    const { data, error } = await supabase.storage.listBuckets()
    const latency = Date.now() - start
    const bucketNames = data?.map(b => b.name) ?? []
    checks.push({
      name: 'supabase:storage',
      status: error ? 'error' : 'ok',
      latency_ms: latency,
      detail: error ? error.message : `Buckets: ${bucketNames.join(', ') || '(none)'}`
    })
  } catch (err) {
    checks.push({
      name: 'supabase:storage',
      status: 'error',
      detail: err instanceof Error ? err.message : 'Storage check failed'
    })
  }

  // --- DB tables ---
  try {
    const supabase = await getSupabase(event)
    const tables = ['datasets', 'sessions', 'nodes', 'edges', 'events'] as const
    for (const table of tables) {
      const start = Date.now()
      const { count, error } = await supabase.from(table).select('*', { count: 'exact', head: true })
      const latency = Date.now() - start
      checks.push({
        name: `table:${table}`,
        status: error ? 'error' : 'ok',
        latency_ms: latency,
        detail: error ? error.message : `${count ?? 0} rows (${latency}ms)`
      })
    }
  } catch (err) {
    checks.push({
      name: 'table:check',
      status: 'error',
      detail: err instanceof Error ? err.message : 'Table check failed'
    })
  }

  // --- DuckDB availability ---
  try {
    const { Database } = await import('duckdb-async')
    const start = Date.now()
    const db = await Database.create(':memory:')
    const rows = await db.all('SELECT 42 AS answer')
    await db.close()
    const latency = Date.now() - start
    checks.push({
      name: 'duckdb:engine',
      status: (rows as Record<string, unknown>[])[0]?.answer === 42 ? 'ok' : 'error',
      latency_ms: latency,
      detail: `In-memory query OK (${latency}ms)`
    })
  } catch (err) {
    checks.push({
      name: 'duckdb:engine',
      status: 'error',
      detail: err instanceof Error ? err.message : 'DuckDB init failed'
    })
  }

  const allOk = checks.every(c => c.status === 'ok')
  return { status: allOk ? 'healthy' : 'degraded', checks }
})
