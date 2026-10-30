import type { Ref } from 'vue'
import { authApi } from '@/modules/auth'

/**
 * Token 生命周期管理（auth store）
 *
 * localStorage 读写 + 过期时间跟踪 + 到期前 1 分钟的自动刷新。
 * 逐字迁自 stores/auth.ts：刷新失败时清 token 并复位登录态；
 * refreshTimer 改为工厂闭包内的实例状态（每个 store 一份，语义不变）。
 */

// Token 存储键
const ACCESS_TOKEN_KEY = 'access_token'
const REFRESH_TOKEN_KEY = 'refresh_token'
const SESSION_ID_KEY = 'session_id'
const EXPIRES_AT_KEY = 'expires_at'

export function createTokenManager(deps: {
  expiresAt: Ref<number | null>
  sessionId: Ref<string | null>
  isAuthenticated: Ref<boolean>
  username: Ref<string | null>
}) {
  // 刷新定时器
  let refreshTimer: ReturnType<typeof setTimeout> | null = null

  // Token 管理
  function getAccessToken(): string | null {
    return localStorage.getItem(ACCESS_TOKEN_KEY)
  }

  function getRefreshToken(): string | null {
    return localStorage.getItem(REFRESH_TOKEN_KEY)
  }

  function setTokens(
    accessToken: string,
    refreshToken: string,
    session: string,
    expiresIn: number
  ) {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
    localStorage.setItem(SESSION_ID_KEY, session)

    const expireTime = Date.now() + expiresIn * 1000
    localStorage.setItem(EXPIRES_AT_KEY, expireTime.toString())
    deps.expiresAt.value = expireTime
    deps.sessionId.value = session

    // 设置自动刷新
    setupAutoRefresh(expiresIn)
  }

  function updateAccessToken(accessToken: string, expiresIn: number) {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
    const expireTime = Date.now() + expiresIn * 1000
    localStorage.setItem(EXPIRES_AT_KEY, expireTime.toString())
    deps.expiresAt.value = expireTime

    setupAutoRefresh(expiresIn)
  }

  /** 从 localStorage 恢复过期时间（checkAuth 用） */
  function getStoredExpiresAt(): number | null {
    const stored = localStorage.getItem(EXPIRES_AT_KEY)
    return stored ? parseInt(stored, 10) : null
  }

  /** 从 localStorage 恢复 session id（checkAuth 用） */
  function getStoredSessionId(): string | null {
    return localStorage.getItem(SESSION_ID_KEY)
  }

  function clearTokens() {
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    localStorage.removeItem(SESSION_ID_KEY)
    localStorage.removeItem(EXPIRES_AT_KEY)
    deps.expiresAt.value = null
    deps.sessionId.value = null

    if (refreshTimer) {
      clearTimeout(refreshTimer)
      refreshTimer = null
    }
  }

  // 自动刷新设置
  function setupAutoRefresh(expiresIn: number) {
    if (refreshTimer) {
      clearTimeout(refreshTimer)
    }

    // 在过期前 1 分钟刷新
    const refreshDelay = Math.max((expiresIn - 60) * 1000, 10000)

    refreshTimer = setTimeout(async () => {
      await refreshAccessToken()
    }, refreshDelay)
  }

  // 刷新 Access Token
  async function refreshAccessToken(): Promise<boolean> {
    const refreshToken = getRefreshToken()
    if (!refreshToken) {
      console.log('[Auth] 没有 Refresh Token，无法刷新')
      return false
    }

    try {
      console.log('[Auth] 调用刷新 API')
      const response = await authApi.refresh({ refresh_token: refreshToken })
      updateAccessToken(response.data.access_token, response.data.expires_in)
      // 轮换后的 refresh token 必须立刻替换：旧值只在服务端宽限期内有效
      if (response.data.refresh_token) {
        localStorage.setItem(REFRESH_TOKEN_KEY, response.data.refresh_token)
      }
      console.log('[Auth] 刷新成功，新 Token 有效期', response.data.expires_in, '秒')
      return true
    } catch (error) {
      console.log('[Auth] 刷新 API 失败', error)
      // 刷新失败，清除登录状态
      clearTokens()
      deps.isAuthenticated.value = false
      deps.username.value = null
      return false
    }
  }

  return {
    getAccessToken,
    getRefreshToken,
    getStoredExpiresAt,
    getStoredSessionId,
    setTokens,
    updateAccessToken,
    clearTokens,
    setupAutoRefresh,
    refreshAccessToken,
  }
}
