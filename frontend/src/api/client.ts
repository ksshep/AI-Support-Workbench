import type { AIJob, KnowledgeItem, Page, SearchResult, Ticket, TicketDetail, User } from '@/types'

const base = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

export class ApiError extends Error { constructor(public status: number, message: string) { super(message) } }

function messageFrom(payload: unknown, fallback: string) {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (typeof detail === 'object' && detail && 'message' in detail) return String((detail as { message: unknown }).message)
  }
  return fallback
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = sessionStorage.getItem('support_access_token')
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetch(`${base}${path}`, { ...init, headers }) } catch { throw new ApiError(0, '网络连接失败，请检查服务是否可用') }
  const text = await response.text()
  const payload = text ? JSON.parse(text) : undefined
  if (!response.ok) {
    if (response.status === 401) {
      sessionStorage.removeItem('support_access_token')
      if (window.location.pathname !== '/login') window.location.assign('/login')
    }
    throw new ApiError(response.status, messageFrom(payload, `请求失败（${response.status}）`))
  }
  return payload as T
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })
export const api = {
  login: async (email: string, password: string) => {
    const body = new URLSearchParams({ username: email, password })
    return request<{ access_token: string; user: User }>('/auth/login', { method: 'POST', body, headers: { 'Content-Type': 'application/x-www-form-urlencoded' } })
  },
  me: () => request<User>('/auth/me'),
  listTickets: (params: Record<string, string | number | undefined>) => request<Page<Ticket>>(`/tickets?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]))}`),
  getTicket: (id: string) => request<TicketDetail>(`/tickets/${id}`),
  createTicket: (body: { title: string; description: string; classification?: string }) => request<Ticket>('/tickets', { ...json(body), headers: { 'Idempotency-Key': crypto.randomUUID() } }),
  updateTicket: (id: string, body: Record<string, unknown>) => request<TicketDetail>(`/tickets/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  transition: (id: string, event: string) => request<{ status: string }>(`/tickets/${id}/transition`, json({ event })),
  createReply: (id: string, content: string) => request<{ id: string }>(`/tickets/${id}/replies`, json({ content })),
  reviewReply: (ticketId: string, replyId: string, approved: boolean) => request(`/tickets/${ticketId}/replies/${replyId}/review`, json({ approved })),
  sendReply: (ticketId: string, replyId: string) => request(`/tickets/${ticketId}/replies/${replyId}/send`, { method: 'POST' }),
  triggerAnalysis: (id: string) => request<AIJob>(`/tickets/${id}/ai-analysis/trigger`, { method: 'POST' }),
  getAnalysis: (id: string) => request<AIJob>(`/tickets/${id}/ai-analysis`),
  getEvaluation: (id: string) => request<{ id: string; ticket_id: string; rating: number; comment?: string | null; created_at: string }>(`/tickets/${id}/evaluation`),
  createEvaluation: (id: string, rating: number, comment?: string) => request(`/tickets/${id}/evaluation`, json({ rating, comment: comment || null })),
  listKnowledge: (params: Record<string, string | number | undefined>) => request<Page<KnowledgeItem>>(`/knowledge-items?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]))}`),
  uploadKnowledge: (file: File, title: string) => { const form = new FormData(); form.append('file', file); if (title) form.append('title', title); return request<KnowledgeItem>('/knowledge-items', { method: 'POST', body: form }) },
  deleteKnowledge: (id: string) => request<void>(`/knowledge-items/${id}`, { method: 'DELETE' }),
  searchKnowledge: (query: string, top_k = 5) => request<{ items: SearchResult[] }>('/knowledge-search', json({ query, top_k })),
}
