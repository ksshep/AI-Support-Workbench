<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { BookOpen, Inbox, LayoutDashboard, LogOut, Menu, ShieldCheck, X } from 'lucide-vue-next'
import { auth } from '@/stores/auth'

const router = useRouter(); const open = ref(false)
const isStaff = computed(() => auth.user.value?.role !== 'customer')
const nav = computed(() => isStaff.value ? [{ label: '工单工作台', path: '/workbench', icon: LayoutDashboard }, { label: '知识库检索', path: '/knowledge', icon: BookOpen }] : [{ label: '我的工单', path: '/tickets', icon: Inbox }])
function logout() { auth.logout(); router.push('/login') }
</script>

<template>
  <div class="shell">
    <header class="mobile-header"><button class="icon-button" aria-label="打开导航" @click="open = true"><Menu :size="20" /></button><div class="brand-mark"><span>AS</span> AI Support Workbench</div><span class="role-chip">{{ auth.state.user?.role }}</span></header>
    <aside class="sidebar" :class="{ 'is-open': open }">
      <div class="sidebar-top"><div class="brand-mark"><span>AS</span><div>AI Support<br /><strong>Workbench</strong></div></div><button class="icon-button sidebar-close" aria-label="关闭导航" @click="open = false"><X :size="18" /></button></div>
      <div class="rail-label">CURRENT DESK / {{ auth.state.user?.role?.toUpperCase() }}</div>
      <nav class="main-nav" aria-label="主导航"><RouterLink v-for="item in nav" :key="item.path" :to="item.path" class="nav-link" @click="open = false"><component :is="item.icon" :size="18" /><span>{{ item.label }}</span></RouterLink></nav>
      <div class="sidebar-foot"><div class="user-card"><div class="avatar">{{ auth.state.user?.name?.slice(0, 1) }}</div><div class="user-copy"><strong>{{ auth.state.user?.name }}</strong><span>{{ auth.state.user?.email }}</span></div><ShieldCheck :size="15" /></div><button class="logout-button" @click="logout"><LogOut :size="16" /> 退出登录</button></div>
    </aside>
    <main class="main-content"><slot /></main>
  </div>
</template>
