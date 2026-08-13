<script setup lang="ts">
import { CheckCircle2, Clock3, FileCheck2, LoaderCircle, LockKeyhole, OctagonAlert, Send, Sparkles, XCircle } from 'lucide-vue-next'
defineProps<{ value?: string | null }>()
const map: Record<string, { label: string; tone: string; icon: typeof CheckCircle2 }> = {
  open: { label: '待处理', tone: 'blue', icon: Clock3 }, in_review: { label: '审核中', tone: 'amber', icon: FileCheck2 }, replied: { label: '已回复', tone: 'teal', icon: Send }, closed: { label: '已关闭', tone: 'slate', icon: LockKeyhole }, canceled: { label: '已取消', tone: 'red', icon: XCircle }, pending: { label: '排队中', tone: 'amber', icon: Clock3 }, processing: { label: '处理中', tone: 'blue', icon: LoaderCircle }, succeeded: { label: '已完成', tone: 'teal', icon: CheckCircle2 }, failed: { label: '失败', tone: 'red', icon: OctagonAlert }, draft: { label: '草稿', tone: 'slate', icon: Clock3 }, reviewed: { label: '已审核', tone: 'teal', icon: FileCheck2 }, sent: { label: '已发送', tone: 'teal', icon: Send }, ready: { label: '可用', tone: 'teal', icon: CheckCircle2 },
}
</script>
<template><span class="status-badge" :class="`tone-${map[value || '']?.tone || 'slate'}`"><component :is="map[value || '']?.icon || Sparkles" :size="13" />{{ map[value || '']?.label || value || '未知' }}</span></template>
