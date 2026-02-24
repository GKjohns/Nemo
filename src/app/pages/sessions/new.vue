<script setup lang="ts">
import type { Dataset, DatasetProfile, Session, SessionConfig } from '~~/app/types'

definePageMeta({ layout: 'default' })

const router = useRouter()

const step = ref<1 | 2>(1)
const datasets = ref<Dataset[]>([])
const selectedDatasetId = ref<string | undefined>(undefined)
const selectedDatasetProfile = ref<DatasetProfile | null>(null)
const pendingFile = ref<File | null>(null)

const hypothesis = ref('')
const context = ref('')
const advanced = reactive<SessionConfig>({
  max_nodes: 50,
  reflect_every: 5,
  model: 'gpt-5-mini'
})

const loadingDatasets = ref(false)
const loadingProfile = ref(false)
const uploadingDataset = ref(false)
const starting = ref(false)
const error = ref<string | null>(null)

const modelOptions = [
  { label: 'gpt-5-mini', value: 'gpt-5-mini' },
  { label: 'gpt-5', value: 'gpt-5' }
]

const datasetOptions = computed(() => {
  return datasets.value.map(dataset => ({
    id: dataset.id,
    name: dataset.name
  }))
})

const canContinue = computed(() => Boolean(selectedDatasetId.value && selectedDatasetProfile.value))
const canDive = computed(() => Boolean(hypothesis.value.trim().length > 0 && selectedDatasetId.value))

function toErrorMessage(value: unknown): string {
  return value instanceof Error ? value.message : 'Unknown error'
}

async function loadDatasets() {
  loadingDatasets.value = true
  error.value = null
  try {
    const data = await $fetch<{ datasets: Dataset[] }>('/api/datasets')
    datasets.value = data.datasets
  } catch (err) {
    error.value = `Failed to load datasets: ${toErrorMessage(err)}`
  } finally {
    loadingDatasets.value = false
  }
}

async function loadProfile(datasetId: string) {
  loadingProfile.value = true
  error.value = null
  try {
    const data = await $fetch<{ profile: DatasetProfile }>(`/api/datasets/${encodeURIComponent(datasetId)}/profile`)
    selectedDatasetProfile.value = data.profile
  } catch (err) {
    selectedDatasetProfile.value = null
    error.value = `Failed to load dataset profile: ${toErrorMessage(err)}`
  } finally {
    loadingProfile.value = false
  }
}

watch(selectedDatasetId, (next) => {
  selectedDatasetProfile.value = null
  if (!next) return
  void loadProfile(next)
})

function onFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  pendingFile.value = input.files?.[0] ?? null
}

function onDrop(event: DragEvent) {
  event.preventDefault()
  const file = event.dataTransfer?.files?.[0] ?? null
  pendingFile.value = file
}

async function uploadDataset() {
  if (!pendingFile.value) return
  uploadingDataset.value = true
  error.value = null
  try {
    const form = new FormData()
    form.append('file', pendingFile.value)
    const response = await $fetch<{ dataset: Dataset }>('/api/datasets', {
      method: 'POST',
      body: form
    })
    datasets.value = [response.dataset, ...datasets.value.filter(item => item.id !== response.dataset.id)]
    selectedDatasetId.value = response.dataset.id
    pendingFile.value = null
    await loadProfile(response.dataset.id)
  } catch (err) {
    error.value = `Failed to upload dataset: ${toErrorMessage(err)}`
  } finally {
    uploadingDataset.value = false
  }
}

async function createAndDive() {
  if (!selectedDatasetId.value) return
  if (!hypothesis.value.trim()) return
  starting.value = true
  error.value = null
  try {
    const created = await $fetch<{ session: Session }>('/api/sessions', {
      method: 'POST',
      body: {
        dataset_id: selectedDatasetId.value,
        hypothesis: hypothesis.value.trim(),
        context: context.value.trim() || null,
        config: {
          max_nodes: advanced.max_nodes,
          reflect_every: advanced.reflect_every,
          model: advanced.model
        }
      }
    })
    await $fetch(`/api/sessions/${encodeURIComponent(created.session.id)}/dive`, { method: 'POST' })
    await router.push(`/sessions/${created.session.id}`)
  } catch (err) {
    error.value = `Failed to start session: ${toErrorMessage(err)}`
  } finally {
    starting.value = false
  }
}

onMounted(() => {
  void loadDatasets()
})
</script>

<template>
  <UDashboardPanel id="new-session">
    <template #header>
      <UDashboardNavbar title="New Session" description="Choose a dataset, define a hypothesis, and start a dive.">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="mx-auto w-full max-w-5xl space-y-4 p-4">
        <UAlert v-if="error" color="error" title="Could not start session" :description="error" />

        <div class="flex items-center gap-2">
          <UButton :color="step === 1 ? 'primary' : 'neutral'" variant="soft" label="1. Dataset" @click="step = 1" />
          <UButton :color="step === 2 ? 'primary' : 'neutral'" variant="soft" label="2. Hypothesis" :disabled="!canContinue" @click="step = 2" />
        </div>

        <section v-if="step === 1" class="mx-auto w-full max-w-3xl space-y-4 rounded-lg border border-default p-4">
          <h2 class="text-sm font-semibold">Step 1: Select or upload dataset</h2>

          <UFormField label="Dataset library">
            <USelectMenu
              v-model="selectedDatasetId"
              value-key="id"
              label-key="name"
              :items="datasetOptions"
              :loading="loadingDatasets"
              class="w-full"
              placeholder="Choose an existing dataset"
            />
          </UFormField>

          <div
            class="rounded-lg border border-dashed border-default p-5"
            @dragover.prevent
            @drop="onDrop"
          >
            <p class="text-sm font-medium">Upload CSV</p>
            <p class="text-xs text-muted">Drag and drop a CSV here, or choose a file manually.</p>
            <div class="mt-3 flex flex-wrap items-center gap-2">
              <input accept=".csv,text/csv" type="file" class="text-sm" @change="onFileSelect">
              <UButton label="Upload dataset" icon="i-lucide-upload" :disabled="!pendingFile" :loading="uploadingDataset" @click="uploadDataset" />
            </div>
            <p v-if="pendingFile" class="mt-2 text-xs text-muted">Selected: {{ pendingFile.name }}</p>
          </div>

          <div v-if="loadingProfile" class="rounded-md border border-default bg-default/30 p-3 text-sm text-muted">
            Loading schema summary...
          </div>

          <div v-else-if="selectedDatasetProfile" class="space-y-3 rounded-lg border border-default bg-default/20 p-4">
            <div class="flex flex-wrap items-center gap-2 text-xs">
              <UBadge color="neutral" variant="soft" :label="`${selectedDatasetProfile.row_count} rows`" />
              <UBadge color="neutral" variant="soft" :label="`${selectedDatasetProfile.columns.length} columns`" />
            </div>

            <div class="overflow-auto rounded border border-default">
              <table class="w-full text-xs">
                <thead class="bg-default">
                  <tr>
                    <th class="px-3 py-2 text-left font-semibold">Column</th>
                    <th class="px-3 py-2 text-left font-semibold">Type</th>
                    <th class="px-3 py-2 text-left font-semibold">Sample values</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="column in selectedDatasetProfile.columns.slice(0, 12)" :key="column.name" class="border-t border-default">
                    <td class="px-3 py-2">{{ column.name }}</td>
                    <td class="px-3 py-2">{{ column.dtype }}</td>
                    <td class="px-3 py-2">{{ column.sample_values.join(', ') || 'n/a' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div class="flex justify-end">
            <UButton label="Continue to hypothesis" icon="i-lucide-arrow-right" color="primary" :disabled="!canContinue" @click="step = 2" />
          </div>
        </section>

        <section v-else class="mx-auto w-full max-w-3xl space-y-4 rounded-lg border border-default p-4">
          <h2 class="text-sm font-semibold">Step 2: Define hypothesis</h2>

          <UFormField label="Hypothesis">
            <UTextarea
              v-model="hypothesis"
              :rows="4"
              class="w-full"
              placeholder="Example: Increased distraction events predict lower student productivity."
            />
          </UFormField>

          <UFormField label="Context (optional)">
            <UTextarea
              v-model="context"
              :rows="3"
              class="w-full"
              placeholder="Background context, known caveats, and what you already suspect."
            />
          </UFormField>

          <details class="rounded-lg border border-default p-3">
            <summary class="cursor-pointer text-sm font-medium">Advanced configuration</summary>
            <div class="mt-3 grid gap-3 md:grid-cols-3">
              <UFormField label="Max nodes">
                <UInput v-model.number="advanced.max_nodes" type="number" min="1" step="1" />
              </UFormField>
              <UFormField label="Reflect every">
                <UInput v-model.number="advanced.reflect_every" type="number" min="1" step="1" />
              </UFormField>
              <UFormField label="Model">
                <USelectMenu
                  v-model="advanced.model"
                  value-key="value"
                  label-key="label"
                  :items="modelOptions"
                  class="w-full"
                />
              </UFormField>
            </div>
          </details>

          <div class="flex items-center justify-between">
            <UButton label="Back" icon="i-lucide-arrow-left" variant="soft" @click="step = 1" />
            <UButton label="Dive" icon="i-lucide-play" color="primary" :disabled="!canDive" :loading="starting" @click="createAndDive" />
          </div>
        </section>
      </div>
    </template>
  </UDashboardPanel>
</template>
