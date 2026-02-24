<template>
  <UDashboardPanel id="sessions">
    <template #header>
      <UDashboardNavbar title="Sessions">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>

        <template #right>
          <UButton to="/sessions/new" icon="i-lucide-plus" label="New session" />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="space-y-3 max-w-3xl">
        <p class="text-sm text-muted">
          Session list and status cards will be implemented in a later sprint.
        </p>
      </div>
    </template>
  </UDashboardPanel>
</template>
