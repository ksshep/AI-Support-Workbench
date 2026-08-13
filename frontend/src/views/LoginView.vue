<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, LockKeyhole } from 'lucide-vue-next'
import { ApiError } from '@/api/client'
import { auth } from '@/stores/auth'

const email = ref(''); const password = ref(''); const error = ref(''); const route = useRoute(); const router = useRouter()
async function submit() { error.value = ''; try { await auth.login(email.value, password.value); router.push(String(route.query.redirect || (auth.user.value?.role === 'customer' ? '/tickets' : '/workbench'))) } catch (e) { error.value = e instanceof ApiError ? e.message : '登录失败，请稍后重试' } }
</script>
<template>
  <main class="login-page"><section class="login-aside"><div class="brand-mark"><span>AS</span><div>AI Support<br /><strong>Workbench</strong></div></div><div class="login-aside-copy"><div class="registration-line">OPS / 08 — SUPPORT CONTROL</div><h1>让每一条回复，<br /><em>都有可追溯的依据。</em></h1><p>统一处理工单、AI 辅助分析与人工审核，让客服团队在高频工作中保持清晰与可控。</p></div><div class="login-aside-footer">INTERNAL OPERATIONS CONSOLE <span>v0.1</span></div></section><section class="login-panel"><div class="login-box"><div class="section-kicker">SECURE ACCESS / 01</div><h2>登录工作台</h2><p class="muted">使用你的工作账号继续操作。</p><form @submit.prevent="submit"><label>邮箱地址<input v-model="email" type="email" autocomplete="username" required placeholder="name@company.com" /></label><label>密码<input v-model="password" type="password" autocomplete="current-password" required placeholder="输入密码" /></label><div v-if="error" class="inline-error">{{ error }}</div><button class="button primary full" :disabled="auth.state.loading"><span v-if="auth.state.loading" class="loader" />{{ auth.state.loading ? '正在验证' : '进入工作台' }}<ArrowRight v-if="!auth.state.loading" :size="17" /></button></form><div class="login-note"><LockKeyhole :size="15" /> 登录凭证仅保存在当前浏览器会话中</div></div></section></main>
</template>
