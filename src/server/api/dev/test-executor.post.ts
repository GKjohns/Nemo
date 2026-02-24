import { resolve } from 'node:path'
import { DuckDbExecutor } from '~~/server/core/executor'

export default defineEventHandler(async (event) => {
  const body = await readBody<{ sql?: string, csvPath?: string }>(event)

  const sql = body?.sql?.trim()
  if (!sql) {
    throw createError({ statusCode: 400, statusMessage: 'Missing "sql" in request body' })
  }

  const csvPath = body?.csvPath
    ? resolve(process.cwd(), '..', body.csvPath)
    : resolve(process.cwd(), '..', 'dummy_datasets/student_productivity_distraction_dataset_20000.csv')

  const executor = new DuckDbExecutor({ maxRows: 50 })
  const start = Date.now()
  const result = await executor.run(sql, csvPath)
  const latency = Date.now() - start

  return { result, latency_ms: latency, sql, csv_path: csvPath }
})
