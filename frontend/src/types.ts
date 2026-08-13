export type Role = 'customer' | 'agent' | 'admin'
export type User = { id: string; email: string; name: string; role: Role }
export type Ticket = {
  id: string; title: string; status: string; priority: string; classification: string
  summary: string; sentiment: string; customer_name: string; assignee_name?: string | null
  reply_count: number; created_at: string; updated_at: string
}
export type Reply = { id: string; content: string; status: string; is_ai_suggestion: boolean; is_sent: boolean; sender_name: string; created_at: string }
export type Evaluation = { id: string; ticket_id: string; rating: number; comment?: string | null; created_at: string }
export type TicketDetail = Ticket & { description: string; customer_id: string; assignee_id?: string | null; replies: Reply[]; audit: Audit[]; evaluation?: Evaluation | null }
export type Audit = { action: string; old_value?: Record<string, unknown> | null; new_value?: Record<string, unknown> | null; created_at: string }
export type AIJob = { ticket_id: string; job_id: string; job_type: string; status: string; retry_count: number; error_message?: string | null; created_at: string; updated_at: string; result?: { category: string; summary: string; priority: string; sentiment: string; confidence: number; reason: string } | null }
export type KnowledgeItem = { id: string; title: string; source_type: string; file_name: string; file_size_bytes: number; status: string; error_message?: string | null; uploaded_by: string; uploader_name?: string; created_at: string; chunk_count?: number; embedding_count?: number }
export type SearchResult = { chunk_id: string; content: string; knowledge_item_id: string; title: string; chunk_index: number; page_number?: number | null; similarity_score: number }
export type Page<T> = { items: T[]; total: number; page: number; page_size: number; pages: number }
