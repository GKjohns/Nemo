import OpenAI from 'openai'
import type {
  DatasetProfile,
  Edge,
  EdgeType,
  GraphContext,
  Node,
  NodeResult
} from '~~/server/core/types'
import {
  sqlPrompt,
  edgePrompt,
  interpretPrompt,
  nextPrompt,
  questionPrompt,
  synthesisPrompt,
  type PromptSpec
} from '~~/server/core/prompts'

const EDGE_TYPES: EdgeType[] = ['supports', 'conflicts', 'refines', 'inspires']

interface Interpretation {
  answer: string
  confidence: number
}

interface Synthesis {
  summary: string
  confidence: number
  supported_by: string[]
}

type EdgeInput = Omit<Edge, 'id' | 'created_at'>
type NextNodeInput = Omit<Node, 'id' | 'created_at'>

export class LLMClient {
  private client: OpenAI
  private reasoningEffort: 'low' | 'medium' | 'high'
  private maxOutputTokens: number

  constructor(
    private apiKey: string,
    private model: string,
    options?: {
      reasoningEffort?: 'low' | 'medium' | 'high'
      maxOutputTokens?: number
    }
  ) {
    this.client = new OpenAI({ apiKey: this.apiKey })
    this.reasoningEffort = options?.reasoningEffort ?? 'medium'
    this.maxOutputTokens = options?.maxOutputTokens ?? 25000
  }

  private async createStructuredResponse<T>(prompt: PromptSpec): Promise<T> {
    const response = await this.client.responses.create({
      model: this.model,
      instructions: prompt.instructions,
      input: prompt.input,
      store: false,
      max_output_tokens: this.maxOutputTokens,
      reasoning: {
        effort: this.reasoningEffort,
        summary: 'auto'
      },
      text: {
        format: {
          type: 'json_schema',
          name: prompt.schemaName,
          strict: true,
          schema: prompt.schema
        }
      }
    })

    const output = response.output_text
    if (!output) {
      throw new Error('OpenAI response did not include output_text.')
    }

    return JSON.parse(output) as T
  }

  async generateQuestion(node: Node, graphContext: GraphContext, profile: DatasetProfile): Promise<string> {
    const parsed = await this.createStructuredResponse<{ question: string }>(
      questionPrompt(node, graphContext, profile)
    )
    return parsed.question.trim()
  }

  async generateSQL(question: string, profile: DatasetProfile): Promise<string> {
    const parsed = await this.createStructuredResponse<{ sql: string }>(
      sqlPrompt(question, profile)
    )
    return parsed.sql
  }

  async interpret(result: NodeResult, question: string): Promise<Interpretation> {
    const parsed = await this.createStructuredResponse<Interpretation>(
      interpretPrompt(result, question)
    )

    return {
      answer: parsed.answer,
      confidence: Math.min(1, Math.max(0, parsed.confidence))
    }
  }

  async classifyEdges(node: Node, graphContext: GraphContext): Promise<EdgeInput[]> {
    const parsed = await this.createStructuredResponse<{
      edges: Array<{ source_id: string, target_id: string, type: EdgeType, reasoning: string | null }>
    }>(edgePrompt(node, graphContext))

    return parsed.edges
      .filter(edge => EDGE_TYPES.includes(edge.type))
      .map(edge => ({
        session_id: node.session_id,
        source_id: edge.source_id,
        target_id: edge.target_id,
        type: edge.type,
        reasoning: edge.reasoning
      }))
  }

  async suggestNext(node: Node, graphContext: GraphContext, profile: DatasetProfile): Promise<NextNodeInput[]> {
    const parsed = await this.createStructuredResponse<{
      nodes: Array<{ type: 'insight', question: string, priority: number }>
    }>(nextPrompt(node, graphContext, profile))

    return parsed.nodes.map(candidate => ({
      session_id: node.session_id,
      type: candidate.type,
      status: 'frontier',
      question: candidate.question,
      code: null,
      result: null,
      answer: null,
      confidence: null,
      viz_spec: null,
      chart_image_url: null,
      summary: null,
      supported_by: null,
      depth: node.depth + 1,
      priority: candidate.priority
    }))
  }

  async synthesize(graphContext: GraphContext, hypothesis: string): Promise<Synthesis> {
    const parsed = await this.createStructuredResponse<Synthesis>(
      synthesisPrompt(graphContext, hypothesis)
    )

    return {
      summary: parsed.summary,
      confidence: Math.min(1, Math.max(0, parsed.confidence)),
      supported_by: parsed.supported_by
    }
  }
}
