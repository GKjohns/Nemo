<script setup lang="ts">
</script>

<template>
  <UApp>
    <UHeader>
      <template #left>
        <NuxtLink to="/" class="font-bold text-lg tracking-tight flex items-center gap-2">
          <NemoLogo size="xs" />
          Nemo
        </NuxtLink>
      </template>

      <template #right>
        <UColorModeButton />

        <UButton
          to="/home"
          label="Dashboard"
          color="neutral"
          variant="ghost"
          trailing-icon="i-lucide-arrow-right"
        />
      </template>
    </UHeader>

    <UMain>
      <slot />
    </UMain>

    <USeparator />

    <UFooter>
      <template #left>
        <p class="text-sm text-muted">
          Nemo &mdash; Autonomous Data Exploration &bull; &copy; {{ new Date().getFullYear() }}
        </p>
      </template>

      <template #right>
        <UColorModeButton />
      </template>
    </UFooter>
  </UApp>
</template>
