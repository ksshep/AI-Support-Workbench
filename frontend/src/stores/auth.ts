import { computed, reactive } from 'vue'
import { api } from '@/api/client'
import type { User } from '@/types'

const state = reactive<{ user: User | null; loading: boolean }>({ user: null, loading: false })
export const auth = {
  state,
  user: computed(() => state.user),
  isAuthenticated: computed(() => Boolean(state.user && sessionStorage.getItem('support_access_token'))),
  async load() { if (!sessionStorage.getItem('support_access_token')) return; try { state.user = await api.me() } catch { this.logout() } },
  async login(email: string, password: string) { state.loading = true; try { const result = await api.login(email, password); sessionStorage.setItem('support_access_token', result.access_token); state.user = result.user } finally { state.loading = false } },
  logout() { sessionStorage.removeItem('support_access_token'); state.user = null },
}
