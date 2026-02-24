import { createSharedComposable } from '@vueuse/core'

const _useApp = () => {
  const router = useRouter()
  const route = useRoute()
  const sidebarOpen = ref(false)

  defineShortcuts({
    'g-s': () => router.push('/sessions'),
    'g-d': () => router.push('/datasets'),
    'g-c': () => router.push('/settings')
  })

  watch(() => route.fullPath, () => {
    sidebarOpen.value = false
  })

  return {
    sidebarOpen
  }
}

export const useApp = createSharedComposable(_useApp)
