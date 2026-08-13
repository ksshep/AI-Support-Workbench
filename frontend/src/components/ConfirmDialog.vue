<script setup lang="ts">
defineProps<{ open: boolean; title: string; message: string }>()
defineEmits<{ confirm: []; cancel: [] }>()
</script>
<template><Teleport to="body"><div v-if="open" class="dialog-backdrop" @click.self="$emit('cancel')"><section class="dialog" role="dialog" aria-modal="true"><div class="dialog-kicker">CONFIRM ACTION</div><h2>{{ title }}</h2><p>{{ message }}</p><div class="dialog-actions"><button class="button secondary" @click="$emit('cancel')">取消</button><button class="button danger" @click="$emit('confirm')">确认继续</button></div></section></div></Teleport></template>
