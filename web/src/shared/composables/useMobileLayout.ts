import { ref, computed, onMounted, onUnmounted, readonly } from 'vue'

/**
 * 响应式断点配置
 * 与 design-tokens.css 的断点保持同步
 */
export const BREAKPOINTS = {
  sm: 640,  // 移动端
  md: 768,  // 平板竖屏
  lg: 1024, // 平板横屏/小屏桌面
  xl: 1280, // 桌面端
} as const

export type BreakpointKey = keyof typeof BREAKPOINTS

/**
 * 布局模式
 */
export type LayoutMode = 'mobile' | 'tablet' | 'desktop'

/**
 * 布局状态接口
 */
export interface LayoutState {
  /** 当前视口宽度 */
  width: number
  /** 当前视口高度 */
  height: number
  /** 布局模式 */
  mode: LayoutMode
  /** 是否为移动端 */
  isMobile: boolean
  /** 是否为平板 */
  isTablet: boolean
  /** 是否为桌面端 */
  isDesktop: boolean
  /** 是否为触摸设备 */
  isTouchDevice: boolean
  /** 是否为横屏 */
  isLandscape: boolean
  /** 是否为竖屏 */
  isPortrait: boolean
}

// 全局响应式状态（单例模式，避免多次监听）
const width = ref(typeof window !== 'undefined' ? window.innerWidth : 1024)
const height = ref(typeof window !== 'undefined' ? window.innerHeight : 768)
const isTouchDevice = ref(false)
let listenerCount = 0
let resizeHandler: (() => void) | null = null

/**
 * 检测是否为触摸设备
 */
function detectTouchDevice(): boolean {
  if (typeof window === 'undefined') return false
  return (
    'ontouchstart' in window ||
    navigator.maxTouchPoints > 0 ||
    // @ts-expect-error - msMaxTouchPoints 是 IE/Edge 特有属性
    navigator.msMaxTouchPoints > 0
  )
}

/**
 * 响应式布局 composable
 *
 * 提供响应式的布局状态检测，包括：
 * - 断点检测（mobile/tablet/desktop）
 * - 触摸设备检测
 * - 屏幕方向检测
 *
 * @example
 * ```vue
 * <script setup>
 * import { useMobileLayout } from '@/shared/composables/useMobileLayout'
 *
 * const { isMobile, isDesktop, mode } = useMobileLayout()
 * </script>
 *
 * <template>
 *   <MobileTabBar v-if="isMobile" />
 *   <AppTopBar v-if="!isMobile" />
 * </template>
 * ```
 */
export function useMobileLayout() {
  // 初始化触摸设备检测
  if (typeof window !== 'undefined' && !isTouchDevice.value) {
    isTouchDevice.value = detectTouchDevice()
  }

  // 布局模式计算
  const mode = computed<LayoutMode>(() => {
    if (width.value < BREAKPOINTS.md) return 'mobile'
    if (width.value < BREAKPOINTS.lg) return 'tablet'
    return 'desktop'
  })

  // 便捷的布局模式检测
  const isMobile = computed(() => mode.value === 'mobile')
  const isTablet = computed(() => mode.value === 'tablet')
  const isDesktop = computed(() => mode.value === 'desktop')

  // 屏幕方向
  const isLandscape = computed(() => width.value > height.value)
  const isPortrait = computed(() => !isLandscape.value)

  // 断点检测辅助函数
  const isAbove = (breakpoint: BreakpointKey) => computed(() => width.value >= BREAKPOINTS[breakpoint])
  const isBelow = (breakpoint: BreakpointKey) => computed(() => width.value < BREAKPOINTS[breakpoint])
  const isBetween = (min: BreakpointKey, max: BreakpointKey) => computed(() =>
    width.value >= BREAKPOINTS[min] && width.value < BREAKPOINTS[max]
  )

  // 生命周期管理
  onMounted(() => {
    if (typeof window === 'undefined') return

    // 首次挂载时更新尺寸
    width.value = window.innerWidth
    height.value = window.innerHeight
    isTouchDevice.value = detectTouchDevice()

    // 使用单例模式管理事件监听
    if (listenerCount === 0) {
      resizeHandler = () => {
        width.value = window.innerWidth
        height.value = window.innerHeight
      }
      window.addEventListener('resize', resizeHandler, { passive: true })
    }
    listenerCount++
  })

  onUnmounted(() => {
    listenerCount--
    if (listenerCount === 0 && resizeHandler) {
      window.removeEventListener('resize', resizeHandler)
      resizeHandler = null
    }
  })

  // 完整的布局状态对象
  const layoutState = computed<LayoutState>(() => ({
    width: width.value,
    height: height.value,
    mode: mode.value,
    isMobile: isMobile.value,
    isTablet: isTablet.value,
    isDesktop: isDesktop.value,
    isTouchDevice: isTouchDevice.value,
    isLandscape: isLandscape.value,
    isPortrait: isPortrait.value,
  }))

  return {
    // 尺寸（只读）
    width: readonly(width),
    height: readonly(height),

    // 布局模式
    mode,
    isMobile,
    isTablet,
    isDesktop,

    // 设备特性
    isTouchDevice: readonly(isTouchDevice),
    isLandscape,
    isPortrait,

    // 断点检测函数
    isAbove,
    isBelow,
    isBetween,

    // 完整状态
    layoutState,

    // 断点常量
    BREAKPOINTS,
  }
}

/**
 * 根据断点返回不同的值
 *
 * @example
 * ```ts
 * const columns = useResponsiveValue({
 *   mobile: 1,
 *   tablet: 2,
 *   desktop: 4
 * })
 * ```
 */
export function useResponsiveValue<T>(values: {
  mobile: T
  tablet?: T
  desktop?: T
}) {
  const { mode } = useMobileLayout()

  return computed<T>(() => {
    switch (mode.value) {
      case 'mobile':
        return values.mobile
      case 'tablet':
        return values.tablet ?? values.mobile
      case 'desktop':
        return values.desktop ?? values.tablet ?? values.mobile
      default:
        // 兜底：mode 将来新增取值时也不会返回 undefined
        return values.mobile
    }
  })
}


