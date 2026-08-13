<script setup lang="ts">
defineProps<{ message: string; tone?: 'success' | 'error' }>()
</script>
<template><div v-if="message" class="toast" :class="`toast-${tone || 'success'}`" role="status">{{ message }}</div></template>
