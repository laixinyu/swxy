declare namespace API {
  type RepositoryStatus = 'pending' | 'parsing' | 'done' | 'failed'

  type Repository = {
    id: string
    created_at: string
    file_name: string
    file_size: number
    status: RepositoryStatus
    chunk_count: number
    // 0～1
    progress: number
    progress_msg?: string | null
    error?: string | null
    updated_at: string
    user_id: string
  }
}
