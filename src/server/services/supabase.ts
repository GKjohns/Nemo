import type { H3Event } from 'h3'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
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
type DbClient = SupabaseClient<Database>

let serviceRoleClient: DbClient | null = null

export async function getSupabase(event: H3Event) {
  return await serverSupabaseClient<Database>(event)
}

export function getSupabaseServiceRole(): DbClient {
  if (serviceRoleClient) {
    return serviceRoleClient
  }

  const supabaseUrl = process.env.SUPABASE_URL
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY
    ?? process.env.SUPABASE_SECRET_KEY
    ?? process.env.SUPABASE_SERVICE_KEY

  if (!supabaseUrl || !serviceRoleKey) {
    throw new Error('Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.')
  }

  serviceRoleClient = createClient<Database>(supabaseUrl, serviceRoleKey, {
    auth: {
      autoRefreshToken: false,
      persistSession: false
    }
  })

  return serviceRoleClient
}
