<script setup lang="ts">
/**
 * 手动任务移动端卡片（ScanPage 移动视图）
 *
 * 纯展示 + click 事件；样式按原 ScanPage 逐条迁移。
 */
import { NIcon, NProgress, NTag } from 'naive-ui'
import { ChevronForwardOutline } from '@vicons/ionicons5'
import type { ManualJob } from '@/modules/scrape/types'
import StatusBadge from '@/shared/components/business/StatusBadge.vue'
import TouchCard from '@/shared/components/base/TouchCard.vue'
import { JOB_STATUS_BADGE, LINK_MODE_LABELS, LINK_MODE_TAG_TYPE } from '@/modules/scrape/constants'
import { LinkMode } from '@/modules/scrape/types'

defineProps<{ job: ManualJob }>()
const emit = defineEmits<{ click: [] }>()

/** 格式化时间（原 ScanPage 实现：字符串切片） */
const formatTime = (time: string | null) => {
  if (!time) return '-'
  return time.replace('T', ' ').slice(0, 19)
}

/** 计算进度百分比（原 ScanPage 实现） */
const getProgressPercent = (job: ManualJob) => {
  if (job.total_count === 0) return 0
  const percent = ((job.success_count + job.skip_count + job.error_count) / job.total_count) * 100
  // 夹在 0-100 之间：数据异常时宁可画满，也不画出越界的进度条
  return Math.min(100, Math.max(0, Math.round(percent)))
}

const statusBadge = (status: ManualJob['status']) =>
  JOB_STATUS_BADGE[status] || { status: 'default' as const, text: status }
</script>

<template>
  <TouchCard clickable class="job-card" @click="emit('click')">
    <div class="job-card-content">
      <div class="job-header">
        <span class="job-id">#{{ job.id }}</span>
        <StatusBadge
          :status="statusBadge(job.status).status"
          :text="statusBadge(job.status).text"
          size="small"
        />
      </div>
      <div class="job-path">{{ job.scan_path }}</div>
      <div class="job-target">
        <span class="label">目标：</span>
        {{ job.target_folder }}
      </div>
      <div class="job-meta">
        <NTag
          :type="LINK_MODE_TAG_TYPE[job.link_mode as LinkMode] || 'default'"
          size="small"
          :bordered="false"
        >
          {{ LINK_MODE_LABELS[job.link_mode as LinkMode] || '未知' }}
        </NTag>
        <span class="job-time">{{ formatTime(job.created_at) }}</span>
      </div>
      <!-- 进度条 -->
      <div v-if="job.status === 'running' || job.total_count > 0" class="job-progress">
        <NProgress
          type="line"
          :percentage="getProgressPercent(job)"
          :status="job.status === 'failed' ? 'error' : job.status === 'success' ? 'success' : 'default'"
          :show-indicator="false"
          :height="6"
        />
        <div class="progress-stats">
          <span class="stat success">{{ job.success_count }}</span>
          <span class="stat skip">{{ job.skip_count }}</span>
          <span class="stat error">{{ job.error_count }}</span>
          <span class="stat total">/ {{ job.total_count }}</span>
        </div>
      </div>
    </div>
    <template #suffix>
      <NIcon :component="ChevronForwardOutline" class="chevron-icon" />
    </template>
  </TouchCard>
</template>

<style scoped>
.job-card {
  border-radius: 12px;
}

.job-card-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.job-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.job-id {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-3);
}

.job-path {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-1);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-target {
  font-size: 13px;
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
}

.job-time {
  color: var(--text-3);
}

.job-progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 4px;
}

.progress-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.chevron-icon {
  color: var(--text-3);
  font-size: 18px;
}

.progress-stats .stat {
  font-weight: 500;
}

.progress-stats .stat.success {
  color: var(--success-500);
}

.progress-stats .stat.skip {
  color: var(--warning-500);
}

.progress-stats .stat.error {
  color: var(--danger-500);
}

.progress-stats .stat.total {
  color: var(--text-3);
}
</style>
