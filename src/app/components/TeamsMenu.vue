<script setup lang="ts">
defineProps<{
  collapsed?: boolean
}>()
</script>

<template>
  <NuxtLink
    to="/"
    class="flex items-center gap-2.5 px-2 py-2 font-semibold text-highlighted hover:opacity-80 transition-opacity"
  >
    <NemoLogo :size="collapsed ? 'sm' : 'sm'" />
    <span v-if="!collapsed" class="text-base tracking-tight">Nemo</span>
  </NuxtLink>
</template>
