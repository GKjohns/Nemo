<script setup lang="ts">
import type { NavigationMenuItem } from '@nuxt/ui'

const { sidebarOpen: open } = useApp()
const isDev = import.meta.dev

const links = [[{
  label: 'Sessions',
  icon: 'i-lucide-house',
  to: '/sessions',
  onSelect: () => {
    open.value = false
  }
}, {
  label: 'Datasets',
  icon: 'i-lucide-database',
  to: '/datasets',
  onSelect: () => {
    open.value = false
  }
}, {
  label: 'Settings',
  icon: 'i-lucide-settings',
  to: '/settings',
  onSelect: () => {
    open.value = false
  }
}], [
  ...(isDev
    ? [{
        label: 'Dev Tools',
        icon: 'i-lucide-wrench',
        to: '/dev',
        onSelect: () => {
          open.value = false
        }
      }]
    : []),
  {
    label: 'Landing Page',
    icon: 'i-lucide-rocket',
    to: '/'
  }
]] satisfies NavigationMenuItem[][]

const groups = computed(() => [{
  id: 'links',
  label: 'Go to',
  items: links.flat()
}])
</script>

<template>
  <UDashboardGroup unit="rem">
    <UDashboardSidebar
      id="default"
      v-model:open="open"
      collapsible
      resizable
      class="bg-elevated/25"
      :ui="{ footer: 'lg:border-t lg:border-default' }"
    >
      <template #header>
        <div class="flex items-center gap-2">
          <NemoLogo size="xs" />
          <span class="font-semibold">Nemo</span>
        </div>
      </template>

      <template #default="{ collapsed }">
        <UDashboardSearchButton :collapsed="collapsed" class="bg-transparent ring-default" />

        <UNavigationMenu
          :collapsed="collapsed"
          :items="links[0]"
          orientation="vertical"
          tooltip
          popover
        />

        <UNavigationMenu
          :collapsed="collapsed"
          :items="links[1]"
          orientation="vertical"
          tooltip
          class="mt-auto"
        />
      </template>

      <template #footer="{ collapsed }">
        <UserMenu :collapsed="collapsed" />
      </template>
    </UDashboardSidebar>

    <UDashboardSearch :groups="groups" />

    <slot />
  </UDashboardGroup>
</template>
