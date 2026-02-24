import type { H3Event } from 'h3'
import type { Database } from '~~/app/types/database.types'
import type { DatasetProfile } from '~~/server/core/types'
import { serverSupabaseClient } from '#supabase/server'

export interface DatasetRow {
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

export type DatasetInsert = Database['public']['Tables']['datasets']['Insert']
export type DatasetProfileRow = Pick<DatasetRow, 'profile'>

export async function getSupabase(event: H3Event) {
  return await serverSupabaseClient<Database>(event)
}
