import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import { historyApi } from '@/modules/history/api'
import type { HistoryRecordDetail } from '@/modules/history/types'

/**
 * 冲突提交（ResolveConflictModal）
 *
 * 重试模式与冲突处理两条提交路径。校验顺序、提示文案、payload 组装
 * 逐字保留（含 emby 分支 file_action 的 skip/overwrite 二值化、
 * episode 兜底 1）。
 *
 * 季/集一律取自用户的选择：解析值只用于在季集选择器里预选，不在提交时覆盖，
 * 否则用户改完季集又会被解析结果改回去（实测：OVA 被解析成 S00 后无法纠正）。
 */
export function useConflictSubmit(options: {
  /** 当前记录 */
  record: () => HistoryRecordDetail | null
  /** 是否重试模式 */
  isRetryMode: () => boolean
  /** 是否需要手动输入 TMDB ID */
  needManualInput: () => boolean
  // 选择状态（ref 由父组件持有，此处直读直写）
  selectedTmdbId: { value: number | null }
  selectedSeason: { value: number }
  selectedEpisode: { value: number | null }
  embyStep: { value: number }
  fileAction: { value: 'overwrite' | 'skip' | 'rename' }
  embyAction: { value: 'skip' | 'force' | 'change' }
  /** 提交成功回调（原 emit('success')） */
  onSuccess: () => void
}) {
  const message = useMessage()
  const loading = ref(false)

  // 提交处理
  const handleSubmit = async () => {
    const record = options.record()
    if (!record) return

    // 重试模式处理
    if (options.isRetryMode()) {
      if (!options.selectedTmdbId.value) {
        message.warning('请选择一个剧集')
        return
      }
      if (!options.selectedEpisode.value) {
        message.warning('请选择一集')
        return
      }

      loading.value = true
      try {
        await historyApi.retryRecord(record.id, {
          tmdb_id: options.selectedTmdbId.value,
          season: options.selectedSeason.value,
          episode: options.selectedEpisode.value,
        })
        message.success('重试成功')
        options.onSuccess()
      } catch (error: unknown) {
        const err = error as { response?: { data?: { detail?: string } } }
        message.error(err.response?.data?.detail || '重试失败')
      } finally {
        loading.value = false
      }
      return
    }

    // 原有冲突处理逻辑
    const conflictType = record.conflict_type
    if (!conflictType) return

    // 验证
    if (conflictType === 'need_selection') {
      if (!options.selectedTmdbId.value) {
        message.warning('请选择一个剧集')
        return
      }
      // 步骤 2 一定会出现（预选不算已选），未选集就不提交
      if (!options.selectedEpisode.value) {
        message.warning('请选择一集')
        return
      }
    }

    if (conflictType === 'need_season_episode') {
      if (!options.selectedEpisode.value) {
        message.warning('请选择一集')
        return
      }
    }

    if (options.needManualInput()) {
      if (!options.selectedTmdbId.value) {
        message.warning('请输入 TMDB ID')
        return
      }
    }

    if (conflictType === 'emby_conflict' && options.embyStep.value === 2) {
      if (!options.selectedEpisode.value) {
        message.warning('请选择一集')
        return
      }
    }

    loading.value = true
    try {
      const season = options.selectedSeason.value
      const episode = options.selectedEpisode.value || 1

      // Emby 冲突处理
      let fileActionValue = conflictType === 'file_conflict' ? options.fileAction.value : null
      if (conflictType === 'emby_conflict') {
        // 步骤2表示选择了更改季/集；此时 season/episode 已是用户所选
        fileActionValue = options.embyAction.value === 'skip' ? 'skip' : 'overwrite'
      }

      await historyApi.resolveConflict(record.id, {
        conflict_type: conflictType,
        tmdb_id: options.selectedTmdbId.value,
        season,
        episode,
        file_action: fileActionValue,
      })
      message.success('处理成功')
      options.onSuccess()
    } catch (error: unknown) {
      const err = error as { response?: { data?: { detail?: string } } }
      message.error(err.response?.data?.detail || '处理失败')
    } finally {
      loading.value = false
    }
  }

  return { loading, handleSubmit }
}
