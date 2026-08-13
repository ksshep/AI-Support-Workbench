<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import AppShell from '@/components/AppShell.vue'
import { auth } from '@/stores/auth'

const route = useRoute()
const ready = ref(false)
onMounted(async () => { await auth.load(); ready.value = true })
</script>

<template>
  <div v-if="!ready" class="app-loading"><span class="loader" /> 正在载入工作台</div>
  <RouterView v-else v-slot="{ Component }">
    <AppShell v-if="auth.state.user && route.meta.public !== true"><component :is="Component" /></AppShell>
    <component :is="Component" v-else />
  </RouterView>
</template>
