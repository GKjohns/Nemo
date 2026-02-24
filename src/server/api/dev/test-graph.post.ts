import { GraphStore } from '~~/server/core/graph'

export default defineEventHandler(async () => {
  const graph = new GraphStore('test-session', 'Test hypothesis: students who sleep more get better grades')
  const log: string[] = []
  const start = Date.now()

  const root = await graph.createNode({
    session_id: 'test-session',
    type: 'hypothesis',
    status: 'complete',
    question: 'Do students who sleep more get better grades?',
    code: null,
    result: null,
    answer: 'Initial hypothesis — needs investigation',
    confidence: null,
    viz_spec: null,
    chart_image_url: null,
    summary: null,
    supported_by: null,
    depth: 0,
    priority: 1.0
  })
  log.push(`Created root hypothesis node: ${root.id}`)

  const child1 = await graph.createNode({
    session_id: 'test-session',
    type: 'insight',
    status: 'frontier',
    question: 'What is the correlation between sleep_hours and final_grade?',
    code: null,
    result: null,
    answer: null,
    confidence: null,
    viz_spec: null,
    chart_image_url: null,
    summary: null,
    supported_by: null,
    depth: 1,
    priority: 0.8
  })
  log.push(`Created frontier insight node: ${child1.id}`)

  const child2 = await graph.createNode({
    session_id: 'test-session',
    type: 'insight',
    status: 'frontier',
    question: 'Does phone usage mediate the relationship between sleep and grades?',
    code: null,
    result: null,
    answer: null,
    confidence: null,
    viz_spec: null,
    chart_image_url: null,
    summary: null,
    supported_by: null,
    depth: 1,
    priority: 0.6
  })
  log.push(`Created frontier insight node: ${child2.id}`)

  const frontier = await graph.selectFrontier()
  log.push(`Selected frontier: ${frontier?.id} (priority ${frontier?.priority}) — "${frontier?.question}"`)

  const updated = await graph.updateNode(child1.id, {
    status: 'exploring'
  })
  log.push(`Updated node ${updated.id} → status: ${updated.status}`)

  const edge = await graph.createEdge({
    session_id: 'test-session',
    source_id: root.id,
    target_id: child1.id,
    type: 'inspires',
    reasoning: 'Root hypothesis spawned this correlation question'
  })
  log.push(`Created edge: ${root.id} --inspires--> ${child1.id}`)

  const context = await graph.getGraphContext()
  log.push(`Graph context: ${context.stats.node_count} nodes, ${context.stats.edge_count} edges, max depth ${context.stats.max_depth}, frontier ${context.stats.frontier_count}`)

  const latency = Date.now() - start

  return {
    log,
    context,
    edge,
    latency_ms: latency
  }
})
