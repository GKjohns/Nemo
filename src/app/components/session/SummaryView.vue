<script setup lang="ts">
import type { Node, Session } from '~~/app/types'

const props = defineProps<{
  session: Session | null
  nodes: Node[]
}>()

const emit = defineEmits<{
  select: [nodeId: string]
}>()

const synthesisNodes = computed(() => {
  return props.nodes
    .filter(node => node.type === 'synthesis')
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
})

const latestSynthesis = computed(() => synthesisNodes.value[0] ?? null)

const keyFindings = computed(() => {
  return props.nodes
    .filter(node => node.type !== 'hypothesis' && node.status === 'complete')
    .sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))
    .slice(0, 8)
})

const hypothesisNode = computed(() => {
  return props.nodes.find(node => node.type === 'hypothesis') ?? null
})

const verdict = computed<'Supported' | 'Refuted' | 'Complicated' | 'Insufficient Evidence'>(() => {
  const narrative = `${latestSynthesis.value?.summary ?? ''} ${latestSynthesis.value?.answer ?? ''}`.toLowerCase()
  if (!narrative.trim()) return 'Insufficient Evidence'
  if (narrative.includes('refut') || narrative.includes('disprov')) return 'Refuted'
  if (narrative.includes('complicat') || narrative.includes('mixed') || narrative.includes('however') || narrative.includes('but')) return 'Complicated'
  if (narrative.includes('support') || narrative.includes('consistent with')) return 'Supported'
  return 'Insufficient Evidence'
})

const verdictColor = computed(() => {
  if (verdict.value === 'Supported') return 'success'
  if (verdict.value === 'Refuted') return 'error'
  if (verdict.value === 'Complicated') return 'warning'
  return 'neutral'
})

function confidenceLabel(node: Node) {
  if (typeof node.confidence !== 'number') return 'n/a'
  return `${Math.round(node.confidence * 100)}%`
}

function buildMarkdown() {
  const lines: string[] = []
  lines.push('# Nemo Session Summary')
  lines.push('')
  lines.push(`- Session: ${props.session?.id ?? 'unknown'}`)
  lines.push(`- Status: ${props.session?.status ?? 'unknown'}`)
  lines.push(`- Verdict: ${verdict.value}`)
  lines.push('')
  lines.push('## Hypothesis')
  lines.push('')
  lines.push(hypothesisNode.value?.question ?? props.session?.hypothesis ?? 'No hypothesis recorded.')
  lines.push('')
  lines.push('## Synthesis Narrative')
  lines.push('')
  lines.push(latestSynthesis.value?.summary ?? latestSynthesis.value?.answer ?? 'No synthesis narrative available.')
  lines.push('')
  lines.push('## Key Findings')
  lines.push('')
  if (keyFindings.value.length === 0) {
    lines.push('- No key findings yet.')
  } else {
    for (const node of keyFindings.value) {
      const title = node.question ?? node.summary ?? node.id
      lines.push(`- (${node.type}, confidence ${confidenceLabel(node)}) ${title}`)
    }
  }
  return lines.join('\n')
}

async function copyMarkdown() {
  if (!navigator?.clipboard) return
  await navigator.clipboard.writeText(buildMarkdown())
}

function downloadMarkdown() {
  const blob = new Blob([buildMarkdown()], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `nemo-session-${props.session?.id ?? 'summary'}.md`
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <section class="space-y-4 rounded-lg border border-default p-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h3 class="text-base font-semibold">Summary View</h3>
      <div class="flex items-center gap-2">
        <UButton label="Copy markdown" icon="i-lucide-copy" variant="soft" size="sm" @click="copyMarkdown" />
        <UButton label="Download markdown" icon="i-lucide-download" variant="soft" size="sm" @click="downloadMarkdown" />
      </div>
    </div>

    <div class="rounded-md border border-default bg-default/20 p-3">
      <div class="mb-2 flex items-center gap-2">
        <p class="text-xs uppercase tracking-wide text-muted">Hypothesis Verdict</p>
        <UBadge :label="verdict" :color="verdictColor" variant="soft" />
      </div>
      <p class="text-sm">{{ hypothesisNode?.question ?? session?.hypothesis ?? 'No hypothesis available.' }}</p>
    </div>

    <div class="rounded-md border border-warning/40 bg-warning/10 p-3">
      <p class="mb-1 text-xs uppercase tracking-wide text-muted">Top-level synthesis</p>
      <p class="text-sm leading-relaxed">
        {{ latestSynthesis?.summary ?? latestSynthesis?.answer ?? 'No synthesis has been generated yet.' }}
      </p>
    </div>

    <div class="space-y-2">
      <h4 class="text-sm font-semibold">Key Findings</h4>
      <p v-if="keyFindings.length === 0" class="text-sm text-muted">No complete findings available yet.</p>
      <button
        v-for="node in keyFindings"
        :key="node.id"
        type="button"
        class="block w-full rounded-md border border-default bg-default/30 px-3 py-2 text-left hover:bg-default/50"
        @click="emit('select', node.id)"
      >
        <div class="mb-1 flex items-center gap-2">
          <UBadge size="xs" color="neutral" variant="soft" :label="node.type" />
          <UBadge size="xs" color="primary" variant="soft" :label="`confidence ${confidenceLabel(node)}`" />
        </div>
        <p class="text-sm font-medium">{{ node.question ?? node.summary ?? node.id }}</p>
      </button>
    </div>
  </section>
</template>
