import type { ColumnProfile, DatasetProfile } from '~~/server/core/types'

function inferDtype(values: unknown[]): string {
  let hasNumber = false
  let hasBoolean = false
  let hasDate = false
  let hasString = false

  for (const value of values) {
    if (value === null || value === undefined || value === '') {
      continue
    }

    const stringValue = String(value).trim()
    if (stringValue.length === 0) {
      continue
    }

    if (!Number.isNaN(Number(stringValue))) {
      hasNumber = true
      continue
    }

    if (stringValue === 'true' || stringValue === 'false') {
      hasBoolean = true
      continue
    }

    if (!Number.isNaN(Date.parse(stringValue))) {
      hasDate = true
      continue
    }

    hasString = true
  }

  if (hasString) return 'string'
  if (hasDate) return 'datetime'
  if (hasBoolean) return 'boolean'
  if (hasNumber) return 'number'
  return 'unknown'
}

function summarizeDistribution(values: unknown[], dtype: string): string {
  const nonNull = values
    .map(value => String(value).trim())
    .filter(value => value.length > 0)

  if (nonNull.length === 0) {
    return 'No non-null values observed.'
  }

  if (dtype === 'number') {
    const nums = nonNull.map(value => Number(value)).filter(value => !Number.isNaN(value))
    if (nums.length === 0) return 'Unable to compute numeric distribution.'

    const min = Math.min(...nums)
    const max = Math.max(...nums)
    const avg = nums.reduce((sum, current) => sum + current, 0) / nums.length
    return `Numeric range ${min}..${max}; average ${avg.toFixed(2)}.`
  }

  if (dtype === 'boolean') {
    const trueCount = nonNull.filter(value => value === 'true').length
    const falseCount = nonNull.length - trueCount
    return `Boolean counts true=${trueCount}, false=${falseCount}.`
  }

  const unique = new Set(nonNull)
  return `${unique.size} unique values across ${nonNull.length} non-null rows.`
}

function buildColumnProfile(name: string, values: unknown[]): ColumnProfile {
  const nulls = values.filter(value => {
    if (value === null || value === undefined) return true
    return String(value).trim().length === 0
  }).length

  const nonNullValues = values.filter(value => {
    if (value === null || value === undefined) return false
    return String(value).trim().length > 0
  })

  const dtype = inferDtype(nonNullValues)
  const sample_values = Array.from(new Set(nonNullValues.map(value => String(value)))).slice(0, 5)

  return {
    name,
    dtype,
    sample_values,
    nulls,
    distribution_summary: summarizeDistribution(values, dtype)
  }
}

export function buildDatasetProfile(records: Record<string, unknown>[]): DatasetProfile {
  const columnNames = new Set<string>()

  for (const row of records) {
    Object.keys(row).forEach(columnName => columnNames.add(columnName))
  }

  const columns = Array.from(columnNames).map((columnName) => {
    const values = records.map(row => row[columnName])
    return buildColumnProfile(columnName, values)
  })

  return {
    columns,
    row_count: records.length,
    relationships: []
  }
}
