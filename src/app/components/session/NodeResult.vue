<script setup lang="ts">
import type { Node } from '~~/app/types'

const props = defineProps<{
  node: Node
}>()

type TableRow = Record<string, unknown>

function normalizeRows(data: unknown): TableRow[] {
  if (Array.isArray(data)) {
    return data.filter(row => row && typeof row === 'object') as TableRow[]
  }

  if (data && typeof data === 'object' && 'rows' in data) {
    const maybeRows = (data as { rows?: unknown }).rows
    if (Array.isArray(maybeRows)) {
      return maybeRows.filter(row => row && typeof row === 'object') as TableRow[]
    }
  }

  return []
}

const resultType = computed(() => props.node.result?.type ?? null)
const rows = computed(() => normalizeRows(props.node.result?.data))
const columns = computed(() => {
  const first = rows.value[0]
  return first ? Object.keys(first) : []
})

const scalarValue = computed(() => {
  const data = props.node.result?.data
  if (typeof data === 'number' || typeof data === 'string') return String(data)
  if (data && typeof data === 'object' && 'value' in data) {
    const value = (data as { value?: unknown }).value
    return value == null ? 'n/a' : String(value)
  }
  return data == null ? 'n/a' : JSON.stringify(data, null, 2)
})

const errorMessage = computed(() => {
  const data = props.node.result?.data
  if (typeof data === 'string') return data
  if (data && typeof data === 'object' && 'message' in data) {
    const message = (data as { message?: unknown }).message
    return typeof message === 'string' ? message : 'Execution error'
  }
  return 'Execution error'
})
</script>

<template>
  <section class="space-y-3">
    <img
      v-if="node.chart_image_url"
      :src="node.chart_image_url"
      alt="Node chart"
      class="w-full rounded-md border border-default bg-default/40"
    >

    <p v-if="!node.result" class="text-sm text-muted">
      Result is not available for this node yet.
    </p>

    <template v-else-if="resultType === 'table'">
      <p class="text-xs text-muted">
        {{ rows.length }} rows
      </p>
      <div class="max-h-[320px] overflow-auto rounded-md border border-default">
        <table class="w-full text-xs">
          <thead class="sticky top-0 bg-default">
            <tr>
              <th v-for="column in columns" :key="column" class="px-3 py-2 text-left font-semibold">
                {{ column }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, rowIndex) in rows" :key="`row-${rowIndex}`" class="border-t border-default">
              <td
                v-for="column in columns"
                :key="`${rowIndex}-${column}`"
                class="max-w-72 truncate px-3 py-2 align-top"
              >
                {{ row[column] }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <div
      v-else-if="resultType === 'scalar' || resultType === 'chart'"
      class="rounded-md border border-default bg-default/40 p-4"
    >
      <p class="text-xs text-muted">Scalar Result</p>
      <p class="mt-1 text-2xl font-semibold">{{ scalarValue }}</p>
    </div>

    <UAlert
      v-else-if="resultType === 'error'"
      color="error"
      title="Execution error"
      :description="errorMessage"
    />

    <pre
      v-else
      class="overflow-auto rounded-md border border-default bg-default/40 p-3 text-xs"
    >{{ JSON.stringify(node.result.data, null, 2) }}</pre>
  </section>
</template>
