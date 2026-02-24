<script setup lang="ts">
import type { Dataset, DatasetProfile } from '~~/app/types'

definePageMeta({ layout: 'default' })

type SampleRow = Record<string, unknown>

interface SampleResponse {
  columns: string[]
  rows: SampleRow[]
}

const datasets = ref<Dataset[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const expandedId = ref<string | null>(null)
const profiles = ref<Record<string, DatasetProfile>>({})
const samples = ref<Record<string, SampleResponse>>({})
const loadingExpanded = ref(false)
const deletingId = ref<string | null>(null)

function toErrorMessage(value: unknown): string {
  return value instanceof Error ? value.message : 'Unknown error'
}

async function loadDatasets() {
  loading.value = true
  error.value = null
  try {
    const data = await $fetch<{ datasets: Dataset[] }>('/api/datasets')
    datasets.value = data.datasets
  } catch (err) {
    error.value = `Failed to load datasets: ${toErrorMessage(err)}`
  } finally {
    loading.value = false
  }
}

async function loadExpandedData(datasetId: string) {
  loadingExpanded.value = true
  error.value = null
  try {
    const [profile, sample] = await Promise.all([
      $fetch<{ profile: DatasetProfile }>(`/api/datasets/${encodeURIComponent(datasetId)}/profile`),
      $fetch<SampleResponse>(`/api/datasets/${encodeURIComponent(datasetId)}/sample?limit=20`)
    ])

    profiles.value = { ...profiles.value, [datasetId]: profile.profile }
    samples.value = { ...samples.value, [datasetId]: sample }
  } catch (err) {
    error.value = `Failed to load dataset detail: ${toErrorMessage(err)}`
  } finally {
    loadingExpanded.value = false
  }
}

async function toggleExpand(datasetId: string) {
  if (expandedId.value === datasetId) {
    expandedId.value = null
    return
  }

  expandedId.value = datasetId
  if (!profiles.value[datasetId] || !samples.value[datasetId]) {
    await loadExpandedData(datasetId)
  }
}

async function deleteDataset(datasetId: string) {
  const ok = window.confirm('Delete this dataset? This cannot be undone.')
  if (!ok) return
  deletingId.value = datasetId
  error.value = null
  try {
    await $fetch(`/api/datasets/${encodeURIComponent(datasetId)}`, { method: 'DELETE' })
    datasets.value = datasets.value.filter(dataset => dataset.id !== datasetId)
    if (expandedId.value === datasetId) {
      expandedId.value = null
    }
  } catch (err) {
    error.value = `Failed to delete dataset: ${toErrorMessage(err)}`
  } finally {
    deletingId.value = null
  }
}

onMounted(() => {
  void loadDatasets()
})
</script>

<template>
  <UDashboardPanel id="datasets">
    <template #header>
      <UDashboardNavbar title="Datasets" description="Browse profiles, schema details, and sample rows.">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton label="Refresh" icon="i-lucide-refresh-cw" variant="soft" :loading="loading" @click="loadDatasets" />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="space-y-4 p-4">
        <UAlert v-if="error" color="error" title="Dataset explorer error" :description="error" />

        <div v-if="loading && datasets.length === 0" class="space-y-2">
          <div v-for="idx in 5" :key="idx" class="h-20 animate-pulse rounded-lg border border-default bg-default/30" />
        </div>

        <div v-else-if="datasets.length === 0" class="rounded-lg border border-dashed border-default p-8 text-center">
          <p class="text-sm text-muted">No datasets uploaded yet. Create one from the new session flow.</p>
          <UButton class="mt-3" to="/sessions/new" label="Upload a dataset" icon="i-lucide-upload" color="primary" />
        </div>

        <div v-else class="space-y-3">
          <article v-for="dataset in datasets" :key="dataset.id" class="rounded-lg border border-default">
            <div class="flex flex-wrap items-center justify-between gap-2 p-3">
              <div class="min-w-0">
                <p class="truncate text-sm font-semibold">{{ dataset.name }}</p>
                <p class="text-xs text-muted">
                  {{ dataset.row_count ?? 'n/a' }} rows · {{ dataset.column_count ?? 'n/a' }} columns · uploaded
                  {{ new Date(dataset.created_at).toLocaleDateString() }}
                </p>
              </div>

              <div class="flex items-center gap-2">
                <UButton
                  :label="expandedId === dataset.id ? 'Collapse' : 'Explore'"
                  icon="i-lucide-chevron-down"
                  size="sm"
                  variant="soft"
                  @click="toggleExpand(dataset.id)"
                />
                <UButton
                  label="Delete"
                  icon="i-lucide-trash-2"
                  size="sm"
                  color="error"
                  variant="soft"
                  :loading="deletingId === dataset.id"
                  @click="deleteDataset(dataset.id)"
                />
              </div>
            </div>

            <div v-if="expandedId === dataset.id" class="space-y-3 border-t border-default p-3">
              <p v-if="loadingExpanded" class="text-sm text-muted">Loading schema and sample rows...</p>

              <template v-else>
                <div class="overflow-auto rounded border border-default">
                  <table class="w-full text-xs">
                    <thead class="bg-default">
                      <tr>
                        <th class="px-3 py-2 text-left font-semibold">Column</th>
                        <th class="px-3 py-2 text-left font-semibold">Type</th>
                        <th class="px-3 py-2 text-left font-semibold">Nulls</th>
                        <th class="px-3 py-2 text-left font-semibold">Sample values</th>
                        <th class="px-3 py-2 text-left font-semibold">Stats</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="column in profiles[dataset.id]?.columns ?? []"
                        :key="`${dataset.id}-${column.name}`"
                        class="border-t border-default"
                      >
                        <td class="px-3 py-2">{{ column.name }}</td>
                        <td class="px-3 py-2">{{ column.dtype }}</td>
                        <td class="px-3 py-2">{{ column.nulls }}</td>
                        <td class="px-3 py-2">{{ column.sample_values.join(', ') || 'n/a' }}</td>
                        <td class="px-3 py-2">{{ column.distribution_summary || 'n/a' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <div class="overflow-auto rounded border border-default">
                  <table class="w-full text-xs">
                    <thead class="bg-default">
                      <tr>
                        <th
                          v-for="column in samples[dataset.id]?.columns ?? []"
                          :key="`${dataset.id}-sample-head-${column}`"
                          class="px-3 py-2 text-left font-semibold"
                        >
                          {{ column }}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(row, rowIdx) in samples[dataset.id]?.rows ?? []"
                        :key="`${dataset.id}-sample-row-${rowIdx}`"
                        class="border-t border-default"
                      >
                        <td
                          v-for="column in samples[dataset.id]?.columns ?? []"
                          :key="`${dataset.id}-${rowIdx}-${column}`"
                          class="max-w-60 truncate px-3 py-2"
                        >
                          {{ row[column] }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </template>
            </div>
          </article>
        </div>
      </div>
    </template>
  </UDashboardPanel>
</template>
