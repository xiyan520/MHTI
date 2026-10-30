/**
 * scrape 域类型（手动任务相关）
 */
import type { StorageLocator } from '@/shared/types/common'

export type ManualJobStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled'

export const LinkMode = {
  HARDLINK: 1,
  MOVE: 2,
  COPY: 3,
  SYMLINK: 4,
} as const

export type LinkMode = (typeof LinkMode)[keyof typeof LinkMode]

export interface ManualJob {
  id: number
  scan_path: string
  target_folder: string
  metadata_dir: string
  link_mode: LinkMode
  delete_empty_parent: boolean
  config_reuse_id: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  status: ManualJobStatus
  success_count: number
  skip_count: number
  error_count: number
  total_count: number
  /** 投递阶段写入；为 null 表示扫描/投递尚未结束 */
  dispatched_count: number | null
  error_message: string | null
}

export interface ManualJobCreate {
  scan_path: string
  target_folder: string
  metadata_dir?: string
  scan_locator?: StorageLocator | null
  target_locator?: StorageLocator | null
  metadata_locator?: StorageLocator | null
  allow_local_output?: boolean
  link_mode?: LinkMode
  delete_empty_parent?: boolean
  config_reuse_id?: number | null
  advanced_settings?: ManualJobAdvancedSettings | null
}

// 手动任务高级设置 - 剧集刮削器
export interface ManualJobAdvancedSettings {
  // 各分类的全局配置开关
  use_global_organize: boolean
  use_global_download: boolean
  use_global_naming: boolean
  use_global_metadata: boolean
  // 整理设置（当 use_global_organize=false 时使用）
  metadata_folder: string
  delete_metadata_on_fail: boolean
  overwrite_video: boolean
  overwrite_image: boolean
  file_size_filter: number
  file_ext_whitelist: string[]
  file_name_blacklist: string[]
  file_sanitize_list: string[]
  // 自动清理
  protect_ext_whitelist: boolean
  delete_by_size: boolean
  delete_by_ext: boolean
  delete_by_name: boolean
  extra_ext_whitelist: string[]
  // 下载设置（当 use_global_download=false 时使用）
  download_poster: boolean
  download_thumb: boolean
  download_fanart: boolean
  // 命名设置（当 use_global_naming=false 时使用）
  series_folder_template: string
  season_folder_template: string
  episode_file_template: string
  // 元数据设置（当 use_global_metadata=false 时使用）
  scrape_title: boolean
  scrape_plot: boolean
  // NFO设置
  nfo_enabled: boolean
}

/** 高级设置表单数据：4 个分类开关由父组件单独持有，不在表单对象内 */
export type AdvancedSettingsForm = Omit<
  ManualJobAdvancedSettings,
  'use_global_organize' | 'use_global_download' | 'use_global_naming' | 'use_global_metadata'
>

export interface ManualJobListResponse {
  jobs: ManualJob[]
  total: number
}
