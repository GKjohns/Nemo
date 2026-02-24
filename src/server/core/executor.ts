import { Database } from 'duckdb-async'
import type { NodeResult } from '~~/server/core/types'

const DEFAULT_MAX_ROWS = 1_000
const TABLE_ALIAS = 'dataset'

interface DuckDbExecutorConfig {
  maxRows?: number
}

function stripComments(sql: string): string {
  return sql
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/--.*$/gm, '')
    .trim()
}

function hasForbiddenKeyword(sql: string): boolean {
  return /\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|comment|vacuum|copy|attach|detach|load|install)\b/i.test(sql)
}

function isSelectStatement(sql: string): boolean {
  return /^(select|with)\b/i.test(sql.trim())
}

function hasLimitClause(sql: string): boolean {
  return /\blimit\s+\d+\b/i.test(sql)
}

function normalizeValue(value: unknown): unknown {
  if (typeof value === 'bigint') return Number(value)
  return value
}

function removeTrailingSemicolon(sql: string): string {
  return sql.trim().replace(/;\s*$/, '')
}

function hasMultipleStatements(sql: string): boolean {
  const cleaned = removeTrailingSemicolon(sql)
  return cleaned.includes(';')
}

function referencesDatasetAlias(sql: string): boolean {
  return new RegExp(`\\b${TABLE_ALIAS}\\b`, 'i').test(sql)
}

function toErrorResult(message: string, detail?: string | null): NodeResult {
  return {
    type: 'error',
    data: { message, detail: detail ?? null }
  }
}

function validate(sql: string): string {
  const cleaned = stripComments(sql)
  if (!cleaned) {
    throw new Error('SQL cannot be empty.')
  }

  if (hasMultipleStatements(cleaned)) {
    throw new Error('Only a single SELECT statement is allowed.')
  }

  if (!isSelectStatement(cleaned)) {
    throw new Error('SQL must be a SELECT statement.')
  }

  if (hasForbiddenKeyword(cleaned)) {
    throw new Error('Only read-only SELECT queries are allowed.')
  }

  if (!referencesDatasetAlias(cleaned)) {
    throw new Error(`SQL must reference the "${TABLE_ALIAS}" table.`)
  }

  return removeTrailingSemicolon(cleaned)
}

export class DuckDbExecutor {
  private readonly maxRows: number

  constructor(config: DuckDbExecutorConfig = {}) {
    this.maxRows = config.maxRows ?? DEFAULT_MAX_ROWS
  }

  async run(sql: string, csvPath: string): Promise<NodeResult> {
    let cleaned: string
    try {
      cleaned = validate(sql)
    } catch (error) {
      return toErrorResult(error instanceof Error ? error.message : 'Invalid SQL query.')
    }

    const hadLimit = hasLimitClause(cleaned)
    const limitedSql = hadLimit ? cleaned : `${cleaned}\nLIMIT ${this.maxRows}`

    let db: Database | null = null
    try {
      db = await Database.create(':memory:')

      await db.exec(`CREATE VIEW "${TABLE_ALIAS}" AS SELECT * FROM read_csv_auto('${csvPath.replace(/'/g, '\'\'')}')`)

      const rows = await db.all(limitedSql) as Record<string, unknown>[]

      if (rows.length === 0) {
        return {
          type: 'table',
          data: { columns: [], rows: [], row_count: 0, truncated: false }
        }
      }

      const firstRow = rows[0]!
      const columns = Object.keys(firstRow)
      const scalarColumn = columns.length === 1 ? columns[0] : null

      if (rows.length === 1 && scalarColumn) {
        return {
          type: 'scalar',
          data: normalizeValue(firstRow[scalarColumn]) ?? null
        }
      }

      return {
        type: 'table',
        data: {
          columns,
          rows: rows.map(row => columns.map(col => normalizeValue(row[col]) ?? null)),
          row_count: rows.length,
          truncated: !hadLimit && rows.length >= this.maxRows
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Query execution failed.'
      return toErrorResult(message)
    } finally {
      if (db) {
        try {
          await db.close()
        } catch { /* ignore close errors */ }
      }
    }
  }
}
