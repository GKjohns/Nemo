import type { DatasetProfile, NodeResult } from '~~/server/core/types'

export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export interface Database {
  public: {
    Tables: {
      datasets: {
        Row: {
          id: string
          name: string
          description: string | null
          source_type: 'csv' | 'postgres' | 'sqlite'
          connection_info: string
          profile: DatasetProfile | null
          row_count: number | null
          column_count: number | null
          created_at: string
        }
        Insert: {
          id?: string
          name: string
          description?: string | null
          source_type?: 'csv' | 'postgres' | 'sqlite'
          connection_info: string
          profile?: DatasetProfile | null
          row_count?: number | null
          column_count?: number | null
          created_at?: string
        }
        Update: {
          id?: string
          name?: string
          description?: string | null
          source_type?: 'csv' | 'postgres' | 'sqlite'
          connection_info?: string
          profile?: DatasetProfile | null
          row_count?: number | null
          column_count?: number | null
          created_at?: string
        }
        Relationships: []
      }
      sessions: {
        Row: {
          id: string
          dataset_id: string
          hypothesis: string
          context: string | null
          status: string
          config: Json
          created_at: string
          updated_at: string
        }
        Insert: {
          id?: string
          dataset_id: string
          hypothesis: string
          context?: string | null
          status?: string
          config: Json
          created_at?: string
          updated_at?: string
        }
        Update: {
          dataset_id?: string
          hypothesis?: string
          context?: string | null
          status?: string
          config?: Json
          updated_at?: string
        }
        Relationships: []
      }
      nodes: {
        Row: {
          id: string
          session_id: string
          type: string
          status: string
          question: string | null
          code: string | null
          result: NodeResult | null
          answer: string | null
          confidence: number | null
          summary: string | null
          supported_by: string[] | null
          depth: number
          priority: number
          created_at: string
        }
        Insert: {
          id?: string
          session_id: string
          type: string
          status?: string
          question?: string | null
          code?: string | null
          result?: NodeResult | null
          answer?: string | null
          confidence?: number | null
          summary?: string | null
          supported_by?: string[] | null
          depth?: number
          priority?: number
          created_at?: string
        }
        Update: {
          session_id?: string
          type?: string
          status?: string
          question?: string | null
          code?: string | null
          result?: NodeResult | null
          answer?: string | null
          confidence?: number | null
          summary?: string | null
          supported_by?: string[] | null
          depth?: number
          priority?: number
          created_at?: string
        }
        Relationships: []
      }
      edges: {
        Row: {
          id: string
          session_id: string
          source_id: string
          target_id: string
          type: string
          reasoning: string | null
          created_at: string
        }
        Insert: {
          id?: string
          session_id: string
          source_id: string
          target_id: string
          type: string
          reasoning?: string | null
          created_at?: string
        }
        Update: {
          session_id?: string
          source_id?: string
          target_id?: string
          type?: string
          reasoning?: string | null
          created_at?: string
        }
        Relationships: []
      }
      events: {
        Row: {
          id: number
          session_id: string
          type: string
          payload: Json
          created_at: string
        }
        Insert: {
          id?: number
          session_id: string
          type: string
          payload: Json
          created_at?: string
        }
        Update: {
          session_id?: string
          type?: string
          payload?: Json
          created_at?: string
        }
        Relationships: []
      }
    }
    Views: Record<string, never>
    Functions: Record<string, never>
    Enums: Record<string, never>
    CompositeTypes: Record<string, never>
  }
}
