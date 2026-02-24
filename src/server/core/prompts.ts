import type {
  DatasetProfile,
  EdgeType,
  GraphContext,
  Node,
  NodeResult,
  NodeType
} from '~~/server/core/types'

export interface PromptSpec {
  instructions: string
  input: string
  schemaName: string
  schema: Record<string, unknown>
}

const SYSTEM_CONTEXT = `You are Nemo, an autonomous data exploration engine. You are given a dataset and a hypothesis, and you systematically investigate it by running queries, interpreting results, and building a connected knowledge graph of evidence.

## How you work

You operate in a loop:
1. SELECT — pick the highest-priority unexplored question (a "frontier" node)
2. EXPLORE — generate a specific, testable question for that node
3. EXECUTE — write a SQL query to answer it against the dataset
4. INTEGRATE — interpret the result, classify how it relates to other findings, and suggest follow-up questions
5. REFLECT — periodically step back and synthesize everything into a narrative verdict

## The knowledge graph

Your exploration builds a directed graph of nodes and edges.

### Nodes
Each node is a single unit of analysis work:
- **hypothesis** — the root node; the user's starting claim (e.g. "The redesign is causing churn")
- **insight** — a question you investigated, with generated code, execution result, answer, and confidence score
- **synthesis** — a periodic summary that combines evidence from multiple insight nodes into a narrative

Node statuses:
- **frontier** — queued for exploration, not yet investigated
- **exploring** — currently being worked on
- **complete** — investigated, has a result and answer
- **dead_end** — investigated but produced no useful signal

### Edges
Edges are the connective tissue — they encode how findings relate:
- **supports** — the target node provides evidence FOR the source node's claim
- **conflicts** — the target node provides evidence AGAINST the source node's claim
- **refines** — the target narrows, qualifies, or adds nuance to the source
- **inspires** — the source raised a new question that the target explores

Edge direction: source_id is where the edge comes FROM, target_id is where it points TO.

### Confidence
Each completed node has a confidence score (0.0 to 1.0) reflecting how strongly its result answers its question. This propagates through edges — a hypothesis with three high-confidence supporting nodes is strong; one with a 0.95 conflict is in trouble.

## Key principles
- Be precise and evidence-first. Every claim should trace back to a query result.
- Prefer breadth near the root before going deep on any one thread.
- Follow uncertainty — investigate the things you're least sure about.
- When results conflict, that's signal, not noise. Dig deeper.
- Output must conform exactly to the requested JSON schema. No markdown fences, no prose outside the JSON object.`

function serializeContext(context: GraphContext): string {
  const completed = context.nodes.filter(n => n.status === 'complete')
  const frontier = context.nodes.filter(n => n.status === 'frontier')
  const deadEnds = context.nodes.filter(n => n.status === 'dead_end')

  const sections = [
    `Hypothesis: "${context.hypothesis}"`,
    `Graph stats: ${context.stats.node_count} nodes, ${context.stats.edge_count} edges, max depth ${context.stats.max_depth}, ${context.stats.frontier_count} frontier nodes`,
    completed.length > 0
      ? `Completed nodes:\n${completed.map(n => `  - [${n.id}] (depth ${n.depth}, confidence ${n.confidence}) Q: "${n.question}" → A: "${n.answer}"`).join('\n')}`
      : 'No completed nodes yet.',
    frontier.length > 0
      ? `Frontier nodes (awaiting exploration):\n${frontier.map(n => `  - [${n.id}] (depth ${n.depth}, priority ${n.priority}) Q: "${n.question}"`).join('\n')}`
      : 'No frontier nodes remaining.',
    deadEnds.length > 0
      ? `Dead ends: ${deadEnds.map(n => `[${n.id}] "${n.question}"`).join(', ')}`
      : null,
    context.edges.length > 0
      ? `Edges:\n${context.edges.map(e => `  - [${e.source_id}] --${e.type}--> [${e.target_id}]${e.reasoning ? ` (${e.reasoning})` : ''}`).join('\n')}`
      : 'No edges yet.'
  ]

  return sections.filter(Boolean).join('\n\n')
}

function serializeProfile(profile: DatasetProfile): string {
  const lines = [
    `Row count: ${profile.row_count}`,
    `Columns (${profile.columns.length}):`,
    ...profile.columns.map(col =>
      `  - ${col.name} (${col.dtype}): ${col.distribution_summary} | ${col.nulls} nulls | samples: ${col.sample_values.slice(0, 3).join(', ')}`
    )
  ]
  if (profile.relationships.length > 0) {
    lines.push(`Detected relationships:`)
    lines.push(...profile.relationships.map(r => `  - ${r.from_column} → ${r.to_column} (${r.type})`))
  }
  return lines.join('\n')
}

// --- JSON Schemas for structured output ---

export const QUESTION_SCHEMA: Record<string, unknown> = {
  type: 'object',
  additionalProperties: false,
  properties: {
    question: { type: 'string' }
  },
  required: ['question']
}

export const SQL_SCHEMA: Record<string, unknown> = {
  type: 'object',
  additionalProperties: false,
  properties: {
    sql: { type: 'string' }
  },
  required: ['sql']
}

export const INTERPRET_SCHEMA: Record<string, unknown> = {
  type: 'object',
  additionalProperties: false,
  properties: {
    answer: { type: 'string' },
    confidence: { type: 'number' }
  },
  required: ['answer', 'confidence']
}

export const EDGE_SCHEMA: Record<string, unknown> = {
  type: 'object',
  additionalProperties: false,
  properties: {
    edges: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          source_id: { type: 'string' },
          target_id: { type: 'string' },
          type: { type: 'string', enum: ['supports', 'conflicts', 'refines', 'inspires'] satisfies EdgeType[] },
          reasoning: { type: ['string', 'null'] }
        },
        required: ['source_id', 'target_id', 'type', 'reasoning']
      }
    }
  },
  required: ['edges']
}

export const NEXT_SCHEMA: Record<string, unknown> = {
  type: 'object',
  additionalProperties: false,
  properties: {
    nodes: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          type: { type: 'string', enum: ['insight'] satisfies NodeType[] },
          question: { type: 'string' },
          priority: { type: 'number' }
        },
        required: ['type', 'question', 'priority']
      }
    }
  },
  required: ['nodes']
}

export const SYNTHESIS_SCHEMA: Record<string, unknown> = {
  type: 'object',
  additionalProperties: false,
  properties: {
    summary: { type: 'string' },
    confidence: { type: 'number' },
    supported_by: {
      type: 'array',
      items: { type: 'string' }
    }
  },
  required: ['summary', 'confidence', 'supported_by']
}

// --- Prompt builders ---

export function questionPrompt(node: Node, graphContext: GraphContext, profile: DatasetProfile): PromptSpec {
  return {
    instructions: [
      SYSTEM_CONTEXT,
      '',
      '## Your task: Question Generation',
      '',
      'You are in the EXPLORE step of the loop. You have been given a frontier node that needs a specific, testable question.',
      '',
      'Look at the current graph state — what has already been explored, what was found, where confidence is low, and what the hypothesis is. Then generate ONE precise analytical question that this node should investigate.',
      '',
      'A good question is:',
      '- Answerable with a SQL query against the dataset',
      '- Not redundant with questions already explored (check the completed nodes)',
      '- Targeted at reducing uncertainty about the hypothesis',
      '- Specific enough to produce a clear yes/no/numeric result',
      '- References ONLY columns that actually exist in the dataset schema (see below)',
      '',
      'IMPORTANT: Only reference columns listed in the dataset schema. Do NOT assume columns exist that are not listed.'
    ].join('\n'),
    input: [
      'Current frontier node being explored:',
      JSON.stringify(node, null, 2),
      '',
      `Dataset schema:\n${serializeProfile(profile)}`,
      '',
      'Current graph state:',
      serializeContext(graphContext)
    ].join('\n'),
    schemaName: 'question_output',
    schema: QUESTION_SCHEMA
  }
}

export function sqlPrompt(question: string, profile: DatasetProfile): PromptSpec {
  return {
    instructions: [
      SYSTEM_CONTEXT,
      '',
      '## Your task: SQL Query Generation',
      '',
      'You are in the EXECUTE step of the loop. You have a specific question and need to write a SQL query to answer it.',
      '',
      'The query runs on DuckDB against a view called `dataset` that contains all the CSV data.',
      '',
      'Rules:',
      '- Write a single SELECT statement (no CTEs wrapping — though WITH clauses within your query are fine)',
      '- Reference the data as `dataset` (e.g. `SELECT * FROM dataset WHERE ...`)',
      '- DuckDB dialect — ONLY use functions listed below. If a function is not listed, assume it does not exist.',
      '',
      '  Aggregates: COUNT, SUM, AVG, MIN, MAX, STDDEV, STDDEV_POP, STDDEV_SAMP, VARIANCE, VAR_POP, VAR_SAMP, MEDIAN, QUANTILE_CONT, QUANTILE_DISC, MODE, APPROX_COUNT_DISTINCT, ARG_MIN, ARG_MAX, CORR, COVAR_POP, COVAR_SAMP, REGR_SLOPE, REGR_INTERCEPT, REGR_R2, REGR_COUNT, REGR_SXX, REGR_SYY, REGR_SXY, REGR_AVGX, REGR_AVGY, STRING_AGG, LIST, ARRAY_AGG, BOOL_AND, BOOL_OR, HISTOGRAM',
      '  Window: ROW_NUMBER, RANK, DENSE_RANK, NTILE, LAG, LEAD, FIRST_VALUE, LAST_VALUE, NTH_VALUE, PERCENT_RANK, CUME_DIST',
      '  Math: ABS, CEIL, FLOOR, ROUND, LN, LOG2, LOG10, POWER, SQRT, SIGN, GREATEST, LEAST, MOD',
      '  String: LENGTH, LOWER, UPPER, TRIM, SUBSTR, REPLACE, CONCAT, CONCAT_WS, STARTS_WITH, CONTAINS, REGEXP_MATCHES, REGEXP_EXTRACT, REGEXP_REPLACE, SPLIT_PART, LEFT, RIGHT',
      '  Conditional: CASE, COALESCE, NULLIF, IIF',
      '  Type: CAST, TRY_CAST',
      '',
      '  CRITICAL — these functions DO NOT EXIST in DuckDB (never use them):',
      '    linear_regression, ols, regress, predict, fit, ANY_VALUE, PERCENTILE_CONT (use QUANTILE_CONT instead)',
      '',
      '  For correlation/regression use the REGR_* aggregate family:',
      '    SELECT REGR_SLOPE(y, x), REGR_INTERCEPT(y, x), REGR_R2(y, x) FROM dataset',
      '  For percentiles: QUANTILE_CONT(column, 0.5) or QUANTILE_DISC(column, 0.25)',
      '- Column names are case-sensitive — use double quotes if needed (e.g. `"Column Name"`)',
      '- Do NOT use INSERT, UPDATE, DELETE, CREATE, DROP, or any DDL/DML',
      '- Keep queries focused and concise — answer the specific question, not everything at once',
      '',
      'Choose the right shape of output:',
      '- For aggregated summaries → return a small result set with meaningful column aliases',
      '- For comparisons → use GROUP BY with clear labels',
      '- For single metrics → return one row, one column',
      '- For distributions → use histogram buckets or percentiles',
      '',
      'Handle edge cases: use COALESCE for nulls, NULLIF to avoid division by zero, CASE for conditional logic.'
    ].join('\n'),
    input: [
      `Question to answer:\n${question}`,
      '',
      `Dataset schema:\n${serializeProfile(profile)}`
    ].join('\n'),
    schemaName: 'sql_output',
    schema: SQL_SCHEMA
  }
}

export function interpretPrompt(result: NodeResult, question: string): PromptSpec {
  return {
    instructions: [
      SYSTEM_CONTEXT,
      '',
      '## Your task: Result Interpretation',
      '',
      'You are in the INTEGRATE step of the loop. Code was executed to answer a question, and you need to interpret what the result means.',
      '',
      'Provide:',
      '- **answer**: A direct, concise interpretation of what the result tells us about the question. Be specific — reference actual numbers, trends, or patterns from the data. If the result is an error, explain what went wrong and whether the question is still answerable.',
      '- **confidence**: A calibrated score from 0.0 to 1.0 reflecting how definitively the result answers the question:',
      '  - 0.9-1.0: Clear, unambiguous answer with strong signal',
      '  - 0.7-0.9: Good answer but some caveats or noise',
      '  - 0.4-0.7: Partial answer, needs follow-up investigation',
      '  - 0.1-0.4: Weak signal, inconclusive',
      '  - 0.0-0.1: Error or completely uninformative result'
    ].join('\n'),
    input: [
      `Question that was investigated:\n${question}`,
      '',
      `Execution result (type: ${result.type}):`,
      JSON.stringify(result.data, null, 2)
    ].join('\n'),
    schemaName: 'interpret_output',
    schema: INTERPRET_SCHEMA
  }
}

export function edgePrompt(node: Node, graphContext: GraphContext): PromptSpec {
  return {
    instructions: [
      SYSTEM_CONTEXT,
      '',
      '## Your task: Edge Classification',
      '',
      'You are in the INTEGRATE step. A node has just been completed (it has a question, result, answer, and confidence). Now you need to classify how this finding relates to OTHER existing nodes in the graph.',
      '',
      'For each relationship you identify, create an edge with:',
      '- **source_id**: the node this edge comes FROM',
      '- **target_id**: the node this edge points TO',
      '- **type**: one of:',
      '  - "supports" — the target provides evidence FOR the source\'s claim',
      '  - "conflicts" — the target provides evidence AGAINST the source\'s claim',
      '  - "refines" — the target narrows, qualifies, or adds nuance to the source',
      '  - "inspires" — the source raised a question that the target explores',
      '- **reasoning**: a brief explanation of why this relationship exists',
      '',
      'Rules:',
      '- Only include edges that are clearly justified by the evidence in both nodes',
      '- The newly completed node can be either the source or target',
      '- Don\'t create edges between nodes that have no meaningful analytical relationship',
      '- An "inspires" edge typically goes from a parent node to its child',
      '- Return an empty array if there are no meaningful relationships to other nodes'
    ].join('\n'),
    input: [
      'Newly completed node:',
      JSON.stringify({
        id: node.id,
        type: node.type,
        question: node.question,
        answer: node.answer,
        confidence: node.confidence,
        depth: node.depth
      }, null, 2),
      '',
      'Current graph state:',
      serializeContext(graphContext)
    ].join('\n'),
    schemaName: 'edge_output',
    schema: EDGE_SCHEMA
  }
}

export function nextPrompt(node: Node, graphContext: GraphContext, profile: DatasetProfile): PromptSpec {
  return {
    instructions: [
      SYSTEM_CONTEXT,
      '',
      '## Your task: Follow-up Generation',
      '',
      'You are in the INTEGRATE step. A node has just been completed and its edges classified. Now suggest 1-3 follow-up questions worth investigating next.',
      '',
      'Each follow-up becomes a new "insight" node placed on the frontier (queued for future exploration).',
      '',
      'Good follow-ups:',
      '- Dig deeper into surprising or uncertain findings',
      '- Check if a pattern holds across different segments or time periods',
      '- Investigate conflicts — if two findings disagree, what explains the discrepancy?',
      '- Test the opposite of what was found (disconfirmation)',
      '- Explore adjacent questions the current finding implies',
      '',
      'Bad follow-ups:',
      '- Repeating a question that was already explored (check the graph)',
      '- Vague or untestable questions',
      '- Questions unrelated to the hypothesis',
      '- Questions that reference columns not in the dataset schema',
      '',
      'IMPORTANT: Only reference columns that actually exist in the dataset schema (see below). Do NOT invent or assume columns that are not listed.',
      '',
      'For each follow-up, provide:',
      '- **type**: always "insight"',
      '- **question**: specific, testable question text',
      '- **priority**: 0.0-1.0 score reflecting investigation value (higher = should be explored sooner). Low-confidence parents produce high-priority children. Prefer breadth near the root.'
    ].join('\n'),
    input: [
      'Just-completed node:',
      JSON.stringify({
        id: node.id,
        type: node.type,
        question: node.question,
        answer: node.answer,
        confidence: node.confidence,
        depth: node.depth
      }, null, 2),
      '',
      `Dataset schema:\n${serializeProfile(profile)}`,
      '',
      'Current graph state:',
      serializeContext(graphContext)
    ].join('\n'),
    schemaName: 'next_output',
    schema: NEXT_SCHEMA
  }
}

export function synthesisPrompt(graphContext: GraphContext, hypothesis: string): PromptSpec {
  return {
    instructions: [
      SYSTEM_CONTEXT,
      '',
      '## Your task: Synthesis / Reflection',
      '',
      'You are in the REFLECT step. Periodically during exploration, you step back and synthesize everything found so far into a coherent narrative.',
      '',
      'Look at all completed nodes, their answers, confidence scores, and the edge relationships between them. Then produce:',
      '',
      '- **summary**: A concise narrative (2-4 paragraphs) that tells the story of what the data reveals about the hypothesis. Structure it as:',
      '  1. What is the current verdict on the hypothesis? (supported / refuted / complicated / insufficient evidence)',
      '  2. What are the strongest findings and what do they mean?',
      '  3. Where do findings conflict, and what might explain the conflicts?',
      '  4. What key uncertainties remain and what should be investigated next?',
      '',
      '- **confidence**: Overall confidence (0.0-1.0) in the synthesis, reflecting how much of the hypothesis space has been explored and how consistent the evidence is.',
      '',
      '- **supported_by**: Array of node IDs whose findings directly inform this synthesis. Include the most important evidence nodes — not every node, just the ones that meaningfully shape the narrative.'
    ].join('\n'),
    input: [
      `Original hypothesis: "${hypothesis}"`,
      '',
      'Full graph state:',
      serializeContext(graphContext)
    ].join('\n'),
    schemaName: 'synthesis_output',
    schema: SYNTHESIS_SCHEMA
  }
}
