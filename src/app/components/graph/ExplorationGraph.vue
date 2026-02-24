<script setup lang="ts">
import * as d3 from 'd3'
import type { Edge, Node } from '~~/app/types'
import GraphEdge from '~~/app/components/graph/GraphEdge.vue'
import GraphNode from '~~/app/components/graph/GraphNode.vue'

interface ForceNode extends d3.SimulationNodeDatum {
  id: string
  node: Node
}

interface ForceEdge extends d3.SimulationLinkDatum<ForceNode> {
  id: string
  type: Edge['type']
  source: string | ForceNode
  target: string | ForceNode
}

interface RenderEdge {
  id: string
  type: Edge['type']
  x1: number
  y1: number
  x2: number
  y2: number
}

const props = defineProps<{
  nodes: Node[]
  edges: Edge[]
  selectedNodeId: string | null
  exploringNodeId: string | null
}>()

const emit = defineEmits<{
  select: [nodeId: string]
}>()

const containerRef = ref<HTMLElement | null>(null)
const svgRef = ref<SVGSVGElement | null>(null)
const dimensions = reactive({ width: 0, height: 0 })
const pan = ref({ x: 0, y: 0, k: 1 })
const positions = ref<Record<string, { x: number, y: number }>>({})

let simulation: d3.Simulation<ForceNode, ForceEdge> | null = null
let resizeObserver: ResizeObserver | null = null

const renderEdges = computed<RenderEdge[]>(() => {
  return props.edges
    .map((edge) => {
      const source = positions.value[edge.source_id]
      const target = positions.value[edge.target_id]
      if (!source || !target) return null
      return {
        id: edge.id,
        type: edge.type,
        x1: source.x,
        y1: source.y,
        x2: target.x,
        y2: target.y
      }
    })
    .filter((edge): edge is RenderEdge => edge !== null)
})

function updateDimensions() {
  const element = containerRef.value
  if (!element) return
  const box = element.getBoundingClientRect()
  dimensions.width = Math.max(320, Math.round(box.width))
  dimensions.height = Math.max(280, Math.round(box.height))
}

function stopSimulation() {
  if (!simulation) return
  simulation.stop()
  simulation = null
}

function initializeZoom() {
  if (!svgRef.value) return
  d3.select(svgRef.value).call(
    d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.4, 2.5])
      .on('zoom', (event) => {
        pan.value = {
          x: event.transform.x,
          y: event.transform.y,
          k: event.transform.k
        }
      })
  )
}

function rebuildSimulation() {
  if (!dimensions.width || !dimensions.height) return
  if (props.nodes.length === 0) {
    positions.value = {}
    stopSimulation()
    return
  }

  const centerX = dimensions.width / 2
  const centerY = dimensions.height / 2

  const nodes: ForceNode[] = props.nodes.map((node) => {
    const previous = positions.value[node.id]
    return {
      id: node.id,
      node,
      x: previous?.x ?? centerX + (Math.random() - 0.5) * 60,
      y: previous?.y ?? centerY + (Math.random() - 0.5) * 60
    }
  })

  const nodeSet = new Set(nodes.map(node => node.id))
  const edges: ForceEdge[] = props.edges
    .filter(edge => nodeSet.has(edge.source_id) && nodeSet.has(edge.target_id))
    .map(edge => ({
      id: edge.id,
      type: edge.type,
      source: edge.source_id,
      target: edge.target_id
    }))

  stopSimulation()

  simulation = d3
    .forceSimulation<ForceNode>(nodes)
    .force(
      'link',
      d3
        .forceLink<ForceNode, ForceEdge>()
        .id(node => node.id)
        .links(edges)
        .distance(115)
        .strength(0.35)
    )
    .force('charge', d3.forceManyBody().strength(-360))
    .force('center', d3.forceCenter(centerX, centerY))
    .force('collide', d3.forceCollide(30))
    .alpha(0.7)
    .alphaDecay(0.08)
    .on('tick', () => {
      const next: Record<string, { x: number, y: number }> = {}
      for (const node of nodes) {
        next[node.id] = {
          x: node.x ?? centerX,
          y: node.y ?? centerY
        }
      }
      positions.value = next
    })
}

watch(
  () => ({
    nodeSignature: props.nodes.map(node => `${node.id}:${node.status}`).join('|'),
    edgeSignature: props.edges.map(edge => `${edge.id}:${edge.source_id}:${edge.target_id}`).join('|'),
    width: dimensions.width,
    height: dimensions.height
  }),
  () => {
    rebuildSimulation()
  }
)

onMounted(() => {
  updateDimensions()
  initializeZoom()
  rebuildSimulation()
  resizeObserver = new ResizeObserver(() => updateDimensions())
  if (containerRef.value) {
    resizeObserver.observe(containerRef.value)
  }
})

onUnmounted(() => {
  stopSimulation()
  resizeObserver?.disconnect()
})
</script>

<template>
  <section class="h-full min-h-0 rounded-lg border border-default bg-default">
    <header class="border-b border-default px-3 py-2">
      <h3 class="text-sm font-semibold">Exploration Graph</h3>
      <p class="text-xs text-muted">Zoom, pan, and click a node to inspect it.</p>
    </header>

    <div ref="containerRef" class="relative h-[560px] w-full overflow-hidden">
      <div
        v-if="nodes.length === 0"
        class="absolute inset-0 flex items-center justify-center text-sm text-muted"
      >
        Graph will appear once node events stream in.
      </div>

      <svg
        ref="svgRef"
        class="h-full w-full"
        :viewBox="`0 0 ${dimensions.width || 1} ${dimensions.height || 1}`"
      >
        <defs>
          <marker
            id="edge-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" class="text-muted" />
          </marker>
        </defs>

        <g :transform="`translate(${pan.x},${pan.y}) scale(${pan.k})`">
          <GraphEdge
            v-for="edge in renderEdges"
            :key="edge.id"
            :type="edge.type"
            :x1="edge.x1"
            :y1="edge.y1"
            :x2="edge.x2"
            :y2="edge.y2"
          />

          <GraphNode
            v-for="node in nodes"
            :key="node.id"
            :node="node"
            :x="positions[node.id]?.x ?? dimensions.width / 2"
            :y="positions[node.id]?.y ?? dimensions.height / 2"
            :selected="selectedNodeId === node.id"
            :exploring="exploringNodeId === node.id"
            @select="emit('select', $event)"
          />
        </g>
      </svg>
    </div>
  </section>
</template>
