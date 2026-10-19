declare namespace API {
  interface Session {
    created_at: string
    session_id: string
    session_name: string
    updated_at: string
    // user_id: string
  }

  interface ChatItem {
    id: number
    role: import('@/configs').ChatRole
    type: import('@/configs').ChatType
    loading?: boolean
    error?: string
    content?: string
    think?: string
    // 结合上下文改写后、实际拿去检索的问题，和用户原问题不同时才有
    retrieval_query?: string

    documents?: Document[]
    reference?: Reference[]
    recommended_questions?: string[]
  }

  interface Document {
    document_id: string
    document_name: string
    content_with_weight: string
  }

  interface Reference {
    id: string
    document_id: string
    document_name: string
    content_with_weight: string
    positions: number[][]
  }
}
