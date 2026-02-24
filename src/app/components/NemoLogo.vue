<script setup lang="ts">
withDefaults(defineProps<{
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl'
}>(), {
  size: 'md'
})

const dimensions: Record<string, { width: number, height: number }> = {
  xs: { width: 16, height: 22 },
  sm: { width: 20, height: 27 },
  md: { width: 24, height: 32 },
  lg: { width: 32, height: 43 },
  xl: { width: 48, height: 64 }
}
</script>

<template>
  <svg
    xmlns="http://www.w3.org/2000/svg"
    :width="dimensions[size].width"
    :height="dimensions[size].height"
    viewBox="0 0 48 64"
    aria-label="Nemo logo"
  >
    <rect x="4" y="4" width="40" height="56" rx="10" class="logo-bg" />
    <circle cx="24" cy="17" r="3.5" class="logo-dot" opacity="0.25" />
    <circle cx="24" cy="30" r="4" class="logo-dot" opacity="0.45" />
    <circle cx="24" cy="44" r="5" class="logo-dot" />
  </svg>
</template>

<style scoped>
.logo-bg {
  fill: var(--nemo-logo-bg);
}
.logo-dot {
  fill: var(--nemo-logo-dot);
}
</style>
