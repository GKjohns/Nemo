<script setup lang="ts">
const route = useRoute()
</script>

<template>
  <UDashboardPanel id="session-detail">
    <template #header>
      <UDashboardNavbar :title="`Session ${route.params.id}`">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="space-y-3 max-w-3xl">
        <p class="text-sm text-muted">
          Graph exploration view will be implemented in a later sprint.
        </p>
      </div>
    </template>
  </UDashboardPanel>
</template>
