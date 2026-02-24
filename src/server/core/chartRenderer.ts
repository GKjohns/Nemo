import { randomUUID } from 'node:crypto'
import { ChartJSNodeCanvas } from 'chartjs-node-canvas'
import type { ChartConfiguration } from 'chart.js'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import type { NodeResult, VizSpec } from '~~/server/core/types'

const DEFAULT_WIDTH = 1200
const DEFAULT_HEIGHT = 700
const DEFAULT_BUCKET = 'chart-images'
const PALETTE = ['#2563eb', '#dc2626', '#16a34a', '#ca8a04', '#9333ea', '#0891b2', '#ea580c', '#1d4ed8']

interface ChartRendererConfig {
  width?: number
  height?: number
  bucket?: string
  supabaseUrl?: string
  supabaseServiceRoleKey?: string
}

interface TablePayload {
  columns: string[]
  rows: unknown[][]
}

function isTablePayload(value: unknown): value is TablePayload {
  if (!value || typeof value !== 'object') {
    return false
  }

  const payload = value as Record<string, unknown>
  return Array.isArray(payload.columns) && Array.isArray(payload.rows)
}

function valueToNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  if (typeof value === 'string') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }

  return null
}

function rowToObject(columns: string[], row: unknown[]): Record<string, unknown> {
  return columns.reduce<Record<string, unknown>>((acc, column, index) => {
    acc[column] = row[index] ?? null
    return acc
  }, {})
}

function normalizeTable(result: NodeResult): { columns: string[], rows: Record<string, unknown>[] } {
  if (result.type !== 'table' || !isTablePayload(result.data)) {
    throw new Error('ChartRenderer expects a table NodeResult.')
  }

  const columns = result.data.columns.map(column => String(column))
  const rows = result.data.rows
    .filter(row => Array.isArray(row))
    .map(row => rowToObject(columns, row))

  return { columns, rows }
}

function buildChartConfig(rows: Record<string, unknown>[], vizSpec: VizSpec): ChartConfiguration {
  const title = vizSpec.title ?? `${vizSpec.kind.toUpperCase()} chart`
  const xKey = vizSpec.x
  const yKey = vizSpec.y
  const seriesKey = vizSpec.series ?? null

  if (vizSpec.kind === 'scatter') {
    const grouped = new Map<string, Array<{ x: number, y: number }>>()
    rows.forEach((row) => {
      const x = valueToNumber(row[xKey])
      const y = valueToNumber(row[yKey])
      if (x === null || y === null) {
        return
      }

      const groupName = seriesKey ? String(row[seriesKey] ?? 'Series') : 'Series'
      if (!grouped.has(groupName)) {
        grouped.set(groupName, [])
      }
      grouped.get(groupName)?.push({ x, y })
    })

    return {
      type: 'scatter',
      data: {
        datasets: Array.from(grouped.entries()).map(([label, points], index) => ({
          label,
          data: points,
          backgroundColor: PALETTE[index % PALETTE.length]
        }))
      },
      options: {
        responsive: false,
        plugins: {
          title: { display: true, text: title },
          legend: { display: true }
        },
        scales: {
          x: { type: 'linear', title: { display: true, text: xKey } },
          y: { type: 'linear', title: { display: true, text: yKey } }
        }
      }
    }
  }

  const labels = Array.from(new Set(rows.map(row => String(row[xKey] ?? ''))))
  if (seriesKey) {
    const grouped = new Map<string, Map<string, number>>()
    rows.forEach((row) => {
      const groupName = String(row[seriesKey] ?? 'Series')
      const xLabel = String(row[xKey] ?? '')
      const yValue = valueToNumber(row[yKey])
      if (yValue === null) {
        return
      }
      if (!grouped.has(groupName)) {
        grouped.set(groupName, new Map<string, number>())
      }
      grouped.get(groupName)?.set(xLabel, yValue)
    })

    return {
      type: vizSpec.kind,
      data: {
        labels,
        datasets: Array.from(grouped.entries()).map(([label, points], index) => ({
          label,
          data: labels.map(xLabel => points.get(xLabel) ?? null),
          borderColor: PALETTE[index % PALETTE.length],
          backgroundColor: PALETTE[index % PALETTE.length]
        }))
      },
      options: {
        responsive: false,
        plugins: {
          title: { display: true, text: title },
          legend: { display: true }
        },
        scales: {
          x: { title: { display: true, text: xKey } },
          y: { title: { display: true, text: yKey } }
        }
      }
    }
  }

  return {
    type: vizSpec.kind,
    data: {
      labels,
      datasets: [{
        label: yKey,
        data: labels.map((label) => {
          const match = rows.find(row => String(row[xKey] ?? '') === label)
          return valueToNumber(match?.[yKey]) ?? null
        }),
        borderColor: PALETTE[0],
        backgroundColor: PALETTE[0]
      }]
    },
    options: {
      responsive: false,
      plugins: {
        title: { display: true, text: title },
        legend: { display: true }
      },
      scales: {
        x: { title: { display: true, text: xKey } },
        y: { title: { display: true, text: yKey } }
      }
    }
  }
}

export class ChartRenderer {
  private readonly canvas: ChartJSNodeCanvas
  private readonly bucket: string
  private readonly supabase: SupabaseClient

  constructor(config: ChartRendererConfig = {}) {
    const supabaseUrl = config.supabaseUrl ?? process.env.SUPABASE_URL
    const supabaseServiceRoleKey = config.supabaseServiceRoleKey ?? process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.SUPABASE_SERVICE_KEY
    if (!supabaseUrl || !supabaseServiceRoleKey) {
      throw new Error('Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY for ChartRenderer.')
    }

    this.bucket = config.bucket ?? DEFAULT_BUCKET
    this.canvas = new ChartJSNodeCanvas({
      width: config.width ?? DEFAULT_WIDTH,
      height: config.height ?? DEFAULT_HEIGHT,
      backgroundColour: 'white'
    })
    this.supabase = createClient(supabaseUrl, supabaseServiceRoleKey, {
      auth: {
        autoRefreshToken: false,
        persistSession: false
      }
    })
  }

  async render(result: NodeResult, vizSpec: VizSpec): Promise<string> {
    const { rows } = normalizeTable(result)
    const chartConfig = buildChartConfig(rows, vizSpec)
    const png = await this.canvas.renderToBuffer(chartConfig)

    const path = `charts/${Date.now()}-${randomUUID()}.png`
    const upload = await this.supabase.storage
      .from(this.bucket)
      .upload(path, png, {
        contentType: 'image/png',
        upsert: false
      })

    if (upload.error) {
      throw new Error(`Failed to upload chart image: ${upload.error.message}`)
    }

    const publicUrl = this.supabase.storage.from(this.bucket).getPublicUrl(path).data.publicUrl
    if (!publicUrl) {
      throw new Error('Failed to resolve chart image public URL.')
    }

    return publicUrl
  }
}
