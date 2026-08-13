import { createRouter, createWebHistory } from 'vue-router'
import { auth } from '@/stores/auth'
import LoginView from '@/views/LoginView.vue'
import TicketListView from '@/views/TicketListView.vue'
import TicketDetailView from '@/views/TicketDetailView.vue'
import KnowledgeView from '@/views/KnowledgeView.vue'
import ForbiddenView from '@/views/ForbiddenView.vue'
import NotFoundView from '@/views/NotFoundView.vue'

const router = createRouter({ history: createWebHistory(), routes: [
  { path: '/login', component: LoginView, meta: { public: true } },
  { path: '/', redirect: () => auth.state.user?.role === 'customer' ? '/tickets' : '/workbench' },
  { path: '/tickets', component: TicketListView },
  { path: '/workbench', component: TicketListView, meta: { staff: true } },
  { path: '/tickets/:id', component: TicketDetailView },
  { path: '/knowledge', component: KnowledgeView, meta: { staff: true } },
  { path: '/forbidden', component: ForbiddenView, meta: { public: true } },
  { path: '/:pathMatch(.*)*', component: NotFoundView, meta: { public: true } },
] })

router.beforeEach(async (to) => {
  if (!auth.state.user && sessionStorage.getItem('support_access_token')) await auth.load()
  if (to.meta.public) return true
  if (!auth.state.user) return { path: '/login', query: { redirect: to.fullPath } }
  if (to.meta.staff && auth.state.user.role === 'customer') return '/forbidden'
  if (to.path === '/tickets' && auth.state.user.role !== 'customer') return '/workbench'
  return true
})
export default router
