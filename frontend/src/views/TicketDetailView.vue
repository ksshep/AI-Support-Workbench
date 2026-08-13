<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Bot, Check, ChevronRight, CircleAlert, FileText, FileSearch, Play, Send, Sparkles, Star, UserRound, X } from 'lucide-vue-next'
import { api, ApiError } from '@/api/client'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ToastMessage from '@/components/ToastMessage.vue'
import { auth } from '@/stores/auth'
import type { AIJob, ReplySuggestionJob, TicketDetail } from '@/types'

const route = useRoute()
const router = useRouter()
const ticket = ref<TicketDetail | null>(null)
const analysis = ref<AIJob | null>(null)
const suggestion = ref<ReplySuggestionJob | null>(null)
const evaluation = ref<{ rating: number; comment: string } | null>(null)
const loading = ref(true)
const error = ref('')
const actionError = ref('')
const toast = ref('')
const reply = ref('')
const actionLoading = ref(false)
const confirmOpen = ref(false)
const confirmType = ref<'close' | 'send' | 'cancel'>('close')
const evaluationRating = ref(0)
const evaluationComment = ref('')
let analysisPoll: number | undefined
let suggestionPoll: number | undefined

const isStaff = computed(() => auth.user.value?.role !== 'customer')
const isOwner = computed(() => ticket.value?.customer_id === auth.user.value?.id)
const aiBusy = computed(() => Boolean(analysis.value && ['pending', 'processing'].includes(analysis.value.status)))
const suggestionBusy = computed(() => Boolean(suggestion.value && ['pending', 'processing'].includes(suggestion.value.status)))
const draft = computed(() => ticket.value?.replies.find((item) => item.status === 'draft'))

function date(value: string) {
  return new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function loadTicket() {
  ticket.value = await api.getTicket(String(route.params.id))
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    await loadTicket()
    if (isStaff.value) {
      try { analysis.value = await api.getAnalysis(String(route.params.id)) } catch (e) { if (!(e instanceof ApiError && e.status === 404)) throw e }
      try { suggestion.value = await api.getReplySuggestion(String(route.params.id)) } catch (e) { if (!(e instanceof ApiError && e.status === 404)) throw e }
    }
    if (!isStaff.value && isOwner.value && ticket.value?.status === 'closed') {
      try {
        const result = await api.getEvaluation(String(route.params.id))
        evaluation.value = { rating: result.rating, comment: result.comment || '' }
      } catch (e) { if (!(e instanceof ApiError && e.status === 404)) throw e }
    }
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '工单读取失败'
  } finally {
    loading.value = false
  }
  if (aiBusy.value) startAnalysisPolling()
  if (suggestionBusy.value) startSuggestionPolling()
}

async function pollAnalysis() {
  if (!analysis.value || !['pending', 'processing'].includes(analysis.value.status)) { stopAnalysisPolling(); return }
  try {
    analysis.value = await api.getAnalysis(String(route.params.id))
    if (!['pending', 'processing'].includes(analysis.value.status)) stopAnalysisPolling()
  } catch { stopAnalysisPolling() }
}

async function pollSuggestion() {
  if (!suggestion.value || !['pending', 'processing'].includes(suggestion.value.status)) { stopSuggestionPolling(); return }
  try {
    suggestion.value = await api.getReplySuggestion(String(route.params.id))
    if (!['pending', 'processing'].includes(suggestion.value.status)) {
      stopSuggestionPolling()
      if (suggestion.value.status === 'succeeded') await loadTicket()
    }
  } catch { stopSuggestionPolling() }
}

function startAnalysisPolling() { stopAnalysisPolling(); analysisPoll = window.setInterval(pollAnalysis, 3000) }
function stopAnalysisPolling() { if (analysisPoll) { window.clearInterval(analysisPoll); analysisPoll = undefined } }
function startSuggestionPolling() { stopSuggestionPolling(); suggestionPoll = window.setInterval(pollSuggestion, 3000) }
function stopSuggestionPolling() { if (suggestionPoll) { window.clearInterval(suggestionPoll); suggestionPoll = undefined } }

async function transition(event: string) {
  actionLoading.value = true
  actionError.value = ''
  try {
    await api.transition(String(route.params.id), event)
    toast.value = event === 'close' ? '工单已关闭' : event === 'cancel' ? '工单已取消' : '已进入人工审核'
    await load()
  } catch (e) { actionError.value = e instanceof ApiError ? e.message : '状态更新失败' } finally { actionLoading.value = false }
}

async function createReply() {
  if (!reply.value.trim()) return
  actionLoading.value = true
  try { await api.createReply(String(route.params.id), reply.value); reply.value = ''; toast.value = '回复草稿已保存'; await loadTicket() } catch (e) { actionError.value = e instanceof ApiError ? e.message : '回复保存失败' } finally { actionLoading.value = false }
}

async function review(approved: boolean) {
  if (!draft.value) return
  actionLoading.value = true
  try { await api.reviewReply(String(route.params.id), draft.value.id, approved); toast.value = approved ? '回复已通过审核' : '回复已退回草稿'; await loadTicket() } catch (e) { actionError.value = e instanceof ApiError ? e.message : '审核失败' } finally { actionLoading.value = false }
}

async function send() {
  if (!draft.value || draft.value.status !== 'reviewed') return
  actionLoading.value = true
  try { await api.sendReply(String(route.params.id), draft.value.id); toast.value = '回复已发送'; await load() } catch (e) { actionError.value = e instanceof ApiError ? e.message : '发送失败' } finally { actionLoading.value = false }
}

async function runAnalysis() {
  actionLoading.value = true
  actionError.value = ''
  try { analysis.value = await api.triggerAnalysis(String(route.params.id)); toast.value = 'AI 分析已进入队列'; startAnalysisPolling() } catch (e) { actionError.value = e instanceof ApiError ? e.message : '无法触发 AI 分析' } finally { actionLoading.value = false }
}

async function runSuggestion() {
  actionLoading.value = true
  actionError.value = ''
  try { await api.triggerReplySuggestion(String(route.params.id)); suggestion.value = await api.getReplySuggestion(String(route.params.id)); toast.value = 'AI 回复建议已进入队列'; startSuggestionPolling() } catch (e) { actionError.value = e instanceof ApiError ? e.message : '无法触发 AI 回复建议' } finally { actionLoading.value = false }
}

async function evaluate() {
  if (!evaluationRating.value || !isOwner.value) return
  actionLoading.value = true
  actionError.value = ''
  try { await api.createEvaluation(String(route.params.id), evaluationRating.value, evaluationComment.value); evaluation.value = { rating: evaluationRating.value, comment: evaluationComment.value }; toast.value = '感谢你的评价' } catch (e) { actionError.value = e instanceof ApiError ? e.message : '评价提交失败' } finally { actionLoading.value = false }
}

onMounted(async () => { await load() })
onBeforeUnmount(() => { stopAnalysisPolling(); stopSuggestionPolling() })
</script>

<template>
  <div class="page-wrap detail-page">
    <ToastMessage :message="toast" />
    <header class="detail-header"><button class="back-button" @click="router.back()"><ArrowLeft :size="17" /> 返回工单列表</button><div v-if="ticket" class="detail-ref">TICKET / {{ ticket.id.slice(0, 8).toUpperCase() }}</div></header>
    <div v-if="loading" class="detail-loading"><span class="loader" /> 正在读取工单详情</div>
    <div v-else-if="error" class="notice error-notice"><CircleAlert :size="20" /><div><strong>无法打开这条工单</strong><span>{{ error }}</span></div><button class="button secondary" @click="load">重试</button></div>
    <div v-else-if="!ticket"><EmptyState title="工单不存在" message="这条工单可能已被删除，或你没有访问权限。" /></div>
    <template v-else>
      <section class="detail-title-row"><div><div class="section-kicker">{{ ticket.classification || 'UNCLASSIFIED' }} / {{ ticket.customer_name }}</div><h1>{{ ticket.title }}</h1><p class="detail-description">{{ ticket.description }}</p></div><div class="detail-status"><StatusBadge :value="ticket.status" /><span class="priority" :class="`priority-${ticket.priority}`"><i />{{ ticket.priority }}</span></div></section>
      <div v-if="actionError" class="notice error-notice compact"><CircleAlert :size="17" /><span>{{ actionError }}</span></div>
      <section v-if="!isStaff && isOwner && ticket.status === 'open'" class="panel customer-action-panel"><div><div class="section-kicker">CUSTOMER ACTION</div><h2>这条工单仍在处理中</h2><p class="muted">如果问题已经解决，你可以取消这条工单。</p></div><button class="button danger" :disabled="actionLoading" @click="confirmType = 'cancel'; confirmOpen = true"><X :size="15" /> 取消工单</button></section>
      <div class="detail-grid">
        <section class="detail-main">
          <section class="panel conversation-panel"><div class="panel-header"><div><div class="section-kicker">CONVERSATION / {{ ticket.replies.length }}</div><h2>回复记录</h2></div><span class="muted">更新于 {{ date(ticket.updated_at) }}</span></div><div v-if="!ticket.replies.length" class="conversation-empty">尚未有可见回复。</div><article v-for="item in ticket.replies" :key="item.id" class="reply-item" :class="{ 'is-ai': item.is_ai_suggestion }"><div class="reply-avatar"><Bot v-if="item.is_ai_suggestion" :size="17" /><UserRound v-else :size="17" /></div><div class="reply-content"><div class="reply-meta"><strong>{{ item.is_ai_suggestion ? 'AI 回复建议' : item.sender_name }}</strong><span><StatusBadge :value="item.status" /> · {{ date(item.created_at) }}</span></div><p>{{ item.content }}</p><div v-if="item.is_ai_suggestion" class="ai-label"><Sparkles :size="13" /> AI 建议，待人工审核</div><div v-if="isStaff && item.status === 'draft'" class="reply-actions"><button class="button secondary" :disabled="actionLoading" @click="review(false)"><X :size="15" /> 退回草稿</button><button class="button primary" :disabled="actionLoading" @click="review(true)"><Check :size="15" /> 通过审核</button></div><button v-if="isStaff && item.status === 'reviewed'" class="button primary reply-send" :disabled="actionLoading || ticket.status !== 'in_review'" @click="confirmType = 'send'; confirmOpen = true"><Send :size="15" /> 发送已审核回复</button></div></article></section>
          <section v-if="isStaff && ['in_review', 'open'].includes(ticket.status)" class="panel compose-panel"><div class="panel-header"><div><div class="section-kicker">MANUAL DRAFT</div><h2>创建人工回复</h2></div><FileText :size="19" class="muted" /></div><textarea v-model="reply" rows="5" placeholder="输入回复内容，保存后需经过人工审核才能发送。" /><div class="compose-foot"><span>{{ reply.length }} / 10000</span><button class="button primary" :disabled="actionLoading || !reply.trim()" @click="createReply"><Send :size="15" /> 保存草稿</button></div></section>
          <section v-if="!isStaff && isOwner && ticket.status === 'closed'" class="panel evaluation-panel"><div class="section-kicker">CUSTOMER FEEDBACK</div><h2>工单评价</h2><div v-if="evaluation" class="evaluation-readonly"><div class="rating-display"><Star v-for="star in 5" :key="star" :size="18" :fill="star <= evaluation.rating ? 'currentColor' : 'none'" :class="{ selected: star <= evaluation.rating }" /></div><p>{{ evaluation.comment || '已提交评价' }}</p></div><form v-else class="evaluation-form" @submit.prevent="evaluate"><div class="rating-row" aria-label="选择 1 到 5 分"><button v-for="star in 5" :key="star" type="button" :aria-label="`${star} 分`" :class="{ selected: evaluationRating >= star }" @click="evaluationRating = star"><Star :size="21" :fill="evaluationRating >= star ? 'currentColor' : 'none'" /></button></div><textarea v-model="evaluationComment" rows="3" maxlength="2000" placeholder="补充你的体验（可选）" /><button class="button primary" :disabled="actionLoading || !evaluationRating">{{ actionLoading ? '提交中…' : '提交评价' }}</button></form></section>
        </section>
        <aside class="detail-side"><section class="panel action-panel"><div class="section-kicker">WORKFLOW / ACTIONS</div><h2>处理动作</h2><button v-if="isStaff && ticket.status === 'open'" class="action-button" :disabled="actionLoading" @click="transition('start_review')"><span class="action-icon"><Play :size="16" /></span><span><strong>开始审核</strong><small>将工单置于审核中</small></span><ChevronRight :size="16" /></button><button v-if="isStaff && ticket.status === 'in_review'" class="action-button" :disabled="actionLoading || Boolean(aiBusy)" @click="runAnalysis"><span class="action-icon ai"><Sparkles :size="16" /></span><span><strong>触发 AI 分析</strong><small>{{ aiBusy ? '任务处理中，自动刷新' : '更新分类、摘要、优先级与情绪' }}</small></span><ChevronRight :size="16" /></button><button v-if="isStaff && ticket.status === 'in_review'" class="action-button" :disabled="actionLoading || Boolean(suggestionBusy) || suggestion?.status === 'succeeded'" @click="runSuggestion"><span class="action-icon ai"><Sparkles :size="16" /></span><span><strong>生成 AI 回复建议</strong><small>{{ suggestionBusy ? '任务处理中，自动刷新' : suggestion?.status === 'succeeded' ? '建议已生成，等待审核' : '基于知识库生成草稿' }}</small></span><ChevronRight :size="16" /></button><button v-if="isStaff && ticket.status === 'replied'" class="action-button" :disabled="actionLoading" @click="confirmType = 'close'; confirmOpen = true"><span class="action-icon"><Check :size="16" /></span><span><strong>关闭工单</strong><small>回复已发送，结束当前流程</small></span><ChevronRight :size="16" /></button><div class="action-rule" /><div class="detail-facts"><div><span>客户</span><strong>{{ ticket.customer_name }}</strong></div><div><span>创建时间</span><strong>{{ date(ticket.created_at) }}</strong></div><div><span>情绪</span><strong>{{ ticket.sentiment || 'neutral' }}</strong></div><div><span>分配给</span><strong>{{ ticket.assignee_name || '未分配' }}</strong></div></div></section>
          <section v-if="isStaff" class="panel ai-panel"><div class="panel-header"><div><div class="section-kicker">AI ANALYSIS</div><h2>分析结果</h2></div><Bot :size="19" class="muted" /></div><div v-if="!analysis" class="ai-empty"><p>当前没有可用的分析任务。</p><button class="button ghost" :disabled="actionLoading" @click="runAnalysis">运行分析</button></div><template v-else><div class="ai-job-row"><StatusBadge :value="analysis.status" /><span>任务 #{{ analysis.job_id.slice(0, 8) }}</span></div><div v-if="analysis.result" class="analysis-result"><div class="analysis-summary">{{ analysis.result.summary }}</div><div class="analysis-tags"><span>{{ analysis.result.category }}</span><span>{{ Math.round(analysis.result.confidence * 100) }}% confidence</span><span>{{ analysis.result.sentiment }}</span></div><p>{{ analysis.result.reason }}</p></div><div v-else-if="analysis.status === 'failed'" class="inline-error">AI 任务未完成，请稍后重试。</div><div v-else class="ai-progress"><span class="loader" /> 正在等待后台任务完成，每 3 秒自动刷新</div></template></section>
          <section v-if="isStaff && suggestion" class="panel ai-panel suggestion-panel"><div class="panel-header"><div><div class="section-kicker">RAG REPLY SUGGESTION</div><h2>回复建议任务</h2></div><FileSearch :size="19" class="muted" /></div><div class="ai-job-row"><StatusBadge :value="suggestion.status" /><span>任务 #{{ suggestion.job_id.slice(0, 8) }}</span></div><div v-if="suggestion.status === 'succeeded'" class="suggestion-result"><p>AI 草稿已生成，必须经过人工审核后才能发送。</p><div v-if="suggestion.reply_id" class="suggestion-reply-id">草稿 #{{ suggestion.reply_id.slice(0, 8) }}</div><div v-if="suggestion.source_refs?.length" class="source-list"><strong>来源依据</strong><span v-for="source in suggestion.source_refs" :key="`${source.knowledge_item_id}-${source.chunk_index}`">{{ source.title }} · CHUNK {{ source.chunk_index }}<template v-if="source.page_number"> · 第 {{ source.page_number }} 页</template></span></div></div><div v-else-if="suggestion.status === 'failed'" class="inline-error">{{ suggestion.error_message || 'AI 回复建议任务失败，请稍后重试。' }}</div><div v-else class="ai-progress"><span class="loader" /> 正在等待后台任务完成，每 3 秒自动刷新</div></section></aside>
      </div>
    </template>
    <ConfirmDialog :open="confirmOpen" :title="confirmType === 'close' ? '关闭这条工单？' : confirmType === 'cancel' ? '取消这条工单？' : '发送已审核回复？'" :message="confirmType === 'close' ? '关闭后客户将无法继续提交回复，请确认当前处理已经完成。' : confirmType === 'cancel' ? '取消后工单将进入已取消状态，无法继续处理。' : '发送后回复将对客户可见，并推动工单进入已回复状态。'" @cancel="confirmOpen = false" @confirm="async () => { confirmOpen = false; confirmType === 'close' ? await transition('close') : confirmType === 'cancel' ? await transition('cancel') : await send() }" />
  </div>
</template>
