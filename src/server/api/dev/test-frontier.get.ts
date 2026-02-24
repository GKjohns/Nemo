import { computePriority } from '~~/server/core/frontier'

interface TestCase {
  label: string
  parentConfidence: number | null
  depth: number
  deadEndSiblings: number
  priority?: number
}

export default defineEventHandler(() => {
  const cases: TestCase[] = [
    { label: 'Root child, unknown confidence', parentConfidence: null, depth: 1, deadEndSiblings: 0 },
    { label: 'Root child, high confidence parent', parentConfidence: 0.95, depth: 1, deadEndSiblings: 0 },
    { label: 'Root child, low confidence parent', parentConfidence: 0.2, depth: 1, deadEndSiblings: 0 },
    { label: 'Deep node (depth 5), low confidence', parentConfidence: 0.3, depth: 5, deadEndSiblings: 0 },
    { label: 'Deep node (depth 5), low confidence, 3 dead siblings', parentConfidence: 0.3, depth: 5, deadEndSiblings: 3 },
    { label: 'Shallow (depth 2), uncertain', parentConfidence: 0.5, depth: 2, deadEndSiblings: 0 },
    { label: 'Shallow (depth 2), uncertain, 2 dead siblings', parentConfidence: 0.5, depth: 2, deadEndSiblings: 2 },
    { label: 'Very deep (depth 10), very uncertain', parentConfidence: 0.1, depth: 10, deadEndSiblings: 0 }
  ]

  const results = cases.map(c => ({
    ...c,
    priority: Math.round(computePriority(c.parentConfidence, c.depth, c.deadEndSiblings) * 1000) / 1000
  }))

  results.sort((a, b) => b.priority - a.priority)

  return { results }
})
