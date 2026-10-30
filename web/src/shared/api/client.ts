import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000, // 60秒，兼容长时间刮削操作
  headers: {
    'Content-Type': 'application/json',
  },
})

// Token 存储键（与 auth store 保持一致）
const ACCESS_TOKEN_KEY = 'access_token'
const REFRESH_TOKEN_KEY = 'refresh_token'
const SESSION_ID_KEY = 'session_id'
const EXPIRES_AT_KEY = 'expires_at'

// Token 刷新状态管理
let isRefreshing = false
let refreshSubscribers: Array<(token: string) => void> = []

// 获取 token
function getToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

// 获取 refresh token
function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

// 获取过期时间
function getExpiresAt(): number | null {
  const expires = localStorage.getItem(EXPIRES_AT_KEY)
  return expires ? parseInt(expires, 10) : null
}

// 检查 token 是否即将过期（提前 2 分钟）
function isTokenExpiringSoon(): boolean {
  const expiresAt = getExpiresAt()
  if (!expiresAt) return false
  // 提前 2 分钟刷新
  return Date.now() > expiresAt - 2 * 60 * 1000
}

// 更新 token
function updateTokens(accessToken: string, expiresIn: number) {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
  const expireTime = Date.now() + expiresIn * 1000
  localStorage.setItem(EXPIRES_AT_KEY, expireTime.toString())
}

// 清除 token
function clearTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem(SESSION_ID_KEY)
  localStorage.removeItem(EXPIRES_AT_KEY)
}

// 添加请求到等待队列
function subscribeTokenRefresh(callback: (token: string) => void) {
  refreshSubscribers.push(callback)
}

// 通知所有等待的请求
function onTokenRefreshed(token: string) {
  refreshSubscribers.forEach((callback) => callback(token))
  refreshSubscribers = []
}

// 刷新 token
async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    console.log('[API] 没有 Refresh Token，无法刷新')
    return null
  }

  try {
    console.log('[API] 开始刷新 Token')
    // 使用原始 axios 避免拦截器循环
    const response = await axios.post('/api/auth/refresh', {
      refresh_token: refreshToken,
    })

    const { access_token, expires_in, refresh_token } = response.data
    updateTokens(access_token, expires_in)
    // 服务端每次刷新都会轮换 refresh token，旧值只在宽限期内可用，必须落盘新值
    if (refresh_token) {
      localStorage.setItem(REFRESH_TOKEN_KEY, refresh_token)
    }
    console.log('[API] Token 刷新成功，有效期', expires_in, '秒')
    return access_token
  } catch (error) {
    console.error('[API] Token 刷新失败', error)
    return null
  }
}

// 请求拦截器
api.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    // 跳过认证相关接口
    const authPaths = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/status']
    if (authPaths.some((path) => config.url?.includes(path))) {
      const token = getToken()
      if (token && !config.url?.includes('/auth/refresh')) {
        config.headers.Authorization = `Bearer ${token}`
      }
      return config
    }

    const token = getToken()
    if (!token) {
      return config
    }

    // 检查 token 是否即将过期
    if (isTokenExpiringSoon()) {
      console.log('[API] Token 即将过期，尝试刷新')

      if (isRefreshing) {
        // 如果正在刷新，等待刷新完成
        console.log('[API] 等待其他请求的刷新完成')
        return new Promise((resolve) => {
          subscribeTokenRefresh((newToken: string) => {
            config.headers.Authorization = `Bearer ${newToken}`
            resolve(config)
          })
        })
      }

      isRefreshing = true
      try {
        const newToken = await refreshAccessToken()
        if (newToken) {
          config.headers.Authorization = `Bearer ${newToken}`
          onTokenRefreshed(newToken)
        } else {
          // 刷新失败，使用旧 token 继续（可能会 401）
          config.headers.Authorization = `Bearer ${token}`
        }
      } finally {
        isRefreshing = false
      }
    } else {
      config.headers.Authorization = `Bearer ${token}`
    }

    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response
  },
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    // 401 未授权处理
    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      // 如果是 refresh 接口失败，清除 token 并跳转登录
      if (originalRequest.url?.includes('/auth/refresh')) {
        console.log('[API] Refresh Token 失效，跳转登录')
        clearTokens()
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
        return Promise.reject(error)
      }

      // 尝试刷新 token 并重试请求
      if (isRefreshing) {
        // 如果正在刷新，等待刷新完成后重试
        return new Promise((resolve, reject) => {
          subscribeTokenRefresh((newToken: string) => {
            originalRequest.headers.Authorization = `Bearer ${newToken}`
            resolve(api(originalRequest))
          })
          // 设置超时，避免无限等待
          setTimeout(() => {
            reject(new Error('Token refresh timeout'))
          }, 10000)
        })
      }

      originalRequest._retry = true
      isRefreshing = true

      try {
        const newToken = await refreshAccessToken()
        if (newToken) {
          originalRequest.headers.Authorization = `Bearer ${newToken}`
          onTokenRefreshed(newToken)
          return api(originalRequest)
        } else {
          // 刷新失败，跳转登录
          console.log('[API] 无法刷新 Token，跳转登录')
          clearTokens()
          if (window.location.pathname !== '/login') {
            window.location.href = '/login'
          }
          return Promise.reject(error)
        }
      } catch (refreshError) {
        console.error('[API] 刷新异常', refreshError)
        clearTokens()
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
        return Promise.reject(error)
      } finally {
        isRefreshing = false
      }
    }

    // 其他错误处理
    const message = (error.response?.data as { detail?: string })?.detail || error.message || '请求失败'
    console.error('[API] 请求错误:', message)
    return Promise.reject(error)
  }
)

export default api
