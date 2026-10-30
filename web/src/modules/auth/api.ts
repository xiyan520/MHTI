import api from '@/shared/api/client'

// 有效期选项
export type ExpireOption = '1h' | '1d' | '7d' | '30d' | 'never'

export interface LoginRequest {
  username: string
  password: string
  expire_option: ExpireOption
  device_name?: string
}

export interface RegisterRequest {
  username: string
  password: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  refresh_expires_in: number
  session_id: string
}

export interface RefreshRequest {
  refresh_token: string
}

export interface RefreshResponse {
  access_token: string
  expires_in: number
  /** 轮换后的 refresh token：拿到就必须替换本地旧值（旧值只在服务端宽限期内可用） */
  refresh_token?: string
}

export interface VerifyResponse {
  valid: boolean
  username: string
  session_id: string
}

export interface AuthStatusResponse {
  initialized: boolean
}

export interface SessionInfo {
  id: string
  device_name: string
  device_type: string
  ip_address: string
  created_at: string
  last_used_at: string
  is_current: boolean
  expires_at: string
}

export interface SessionListResponse {
  sessions: SessionInfo[]
  total: number
}

export interface LoginHistoryItem {
  id: number
  ip_address: string
  device_name: string | null
  user_agent: string | null
  login_time: string
  success: boolean
  failure_reason: string | null
}

export interface LoginHistoryResponse {
  items: LoginHistoryItem[]
  total: number
}

// ========== 账户管理相关类型 ==========

export interface UserProfile {
  username: string
  avatar: string | null
  created_at: string | null
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}

export interface ChangePasswordResponse {
  success: boolean
  message: string
}

export interface UpdateUsernameRequest {
  new_username: string
  password: string
}

export interface UpdateUsernameResponse {
  success: boolean
  message: string
  new_username: string | null
}

export interface UpdateAvatarRequest {
  avatar: string // Base64 编码的图片
}

export interface UpdateAvatarResponse {
  success: boolean
  message: string
  avatar: string | null
}

export const authApi = {
  // 基础认证
  status: () => api.get<AuthStatusResponse>('/auth/status'),
  register: (data: RegisterRequest) => api.post<TokenResponse>('/auth/register', data),
  login: (data: LoginRequest) => api.post<TokenResponse>('/auth/login', data),
  verify: () => api.get<VerifyResponse>('/auth/verify'),
  logout: () => api.post('/auth/logout'),

  // Token 刷新
  refresh: (data: RefreshRequest) => api.post<RefreshResponse>('/auth/refresh', data),

  // 会话管理
  getSessions: () => api.get<SessionListResponse>('/auth/sessions'),
  revokeSession: (sessionId: string) => api.delete(`/auth/sessions/${sessionId}`),
  revokeAllSessions: () => api.delete('/auth/sessions'),

  // 登录历史
  getHistory: (limit = 20, offset = 0) =>
    api.get<LoginHistoryResponse>('/auth/history', { params: { limit, offset } }),

  // 账户管理
  getProfile: () => api.get<UserProfile>('/auth/profile'),
  changePassword: (data: ChangePasswordRequest) =>
    api.put<ChangePasswordResponse>('/auth/password', data),
  updateUsername: (data: UpdateUsernameRequest) =>
    api.put<UpdateUsernameResponse>('/auth/username', data),
  updateAvatar: (data: UpdateAvatarRequest) =>
    api.put<UpdateAvatarResponse>('/auth/avatar', data),
  deleteAvatar: () => api.delete<{ success: boolean; message: string }>('/auth/avatar'),
}

// 有效期选项配置
export const expireOptions: { value: ExpireOption; label: string; description: string }[] = [
  { value: '1h', label: '1 小时', description: '适合公共设备' },
  { value: '1d', label: '1 天', description: '日常使用' },
  { value: '7d', label: '7 天', description: '个人设备（推荐）' },
  { value: '30d', label: '30 天', description: '长期信任设备' },
  { value: 'never', label: '永不过期', description: '私人服务器' },
]
