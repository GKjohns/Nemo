/**
 * Frontier priority scoring.
 *
 * priority = uncertainty × depthPenalty × siblingPenalty
 *
 * - Low-confidence parents → high-priority children (chase uncertainty)
 * - Depth penalty decays gently (prefer breadth near the root)
 * - Dead-end siblings reduce priority (stop hitting the same wall)
 */

const DEPTH_DECAY = 0.2
const SIBLING_PENALTY_RATE = 0.15
const DEFAULT_CONFIDENCE = 0.5

export function computePriority(
  parentConfidence: number | null,
  depth: number,
  deadEndSiblingCount: number = 0
): number {
  const uncertainty = 1 - (parentConfidence ?? DEFAULT_CONFIDENCE)
  const depthPenalty = 1 / (1 + DEPTH_DECAY * depth)
  const siblingPenalty = Math.max(0.1, 1 - SIBLING_PENALTY_RATE * deadEndSiblingCount)

  return Math.max(0, Math.min(1, uncertainty * depthPenalty * siblingPenalty))
}
