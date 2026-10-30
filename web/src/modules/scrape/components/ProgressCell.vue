<script setup lang="ts">
import { computed } from 'vue'
import { NProgress, NTooltip, NIcon } from 'naive-ui'
import {
  CheckmarkCircleOutline,
  CloseCircleOutline,
  RemoveCircleOutline,
  InformationCircleOutline,
} from '@vicons/ionicons5'

const props = defineProps<{
  successCount: number
  skipCount: number
  errorCount: number
  totalCount: number
  showLabel?: boolean
}>()

// 已处理 = 成功 + 跳过 + 错误
const completedCount = computed(
  () => props.successCount + props.skipCount + props.errorCount
)

// 进度百分比：夹在 0-100 之间，脏数据也不会画出越界的进度条
const progressPercentage = computed(() => {
  if (props.totalCount === 0) return 0
  const percent = Math.round((completedCount.value / props.totalCount) * 100)
  return Math.min(100, Math.max(0, percent))
})

// 成功率
const successRate = computed(() => {
  const completed = props.successCount + props.errorCount
  if (completed === 0) return 0
  return Math.round((props.successCount / completed) * 100)
})

// 进度条类型
const progressStatus = computed(() => {
  if (props.errorCount > 0 && props.successCount === 0) return 'error'
  if (props.errorCount > 0) return 'warning'
  return 'success'
})

// 详细统计
const stats = computed(() => [
  { label: '成功', value: props.successCount, color: 'var(--success-500)', icon: CheckmarkCircleOutline },
  { label: '跳过', value: props.skipCount, color: 'var(--text-2)', icon: RemoveCircleOutline },
  { label: '错误', value: props.errorCount, color: 'var(--danger-500)', icon: CloseCircleOutline },
])
</script>

<template>
  <div class="progress-cell">
    <NTooltip placement="top">
      <template #trigger>
        <div class="progress-wrapper">
          <NProgress
            type="line"
            :percentage="progressPercentage"
            :status="progressStatus"
            :height="6"
            :border-radius="3"
            :show-indicator="false"
          />
          <div class="progress-stats">
            <span
              v-for="stat in stats"
              :key="stat.label"
              class="stat-item"
              :style="{ color: stat.color }"
            >
              <NIcon :component="stat.icon" :size="12" />
              <span>{{ stat.value }}</span>
            </span>
          </div>
        </div>
      </template>
      <div class="tooltip-content">
        <div class="tooltip-header">
          <NIcon :component="InformationCircleOutline" />
          <span>任务进度详情</span>
        </div>
        <div class="tooltip-row">
          <span>完成进度</span>
          <span class="tooltip-value">{{ progressPercentage }}%</span>
        </div>
        <div class="tooltip-row">
          <span>已处理</span>
          <span class="tooltip-value">{{ completedCount }} / {{ totalCount }}</span>
        </div>
        <div class="tooltip-row">
          <span>成功率</span>
          <span class="tooltip-value">{{ successRate }}%</span>
        </div>
        <div class="tooltip-divider"></div>
        <div v-for="stat in stats" :key="stat.label" class="tooltip-row">
          <span :style="{ color: stat.color }">
            <NIcon :component="stat.icon" style="margin-right: 4px" />
            {{ stat.label }}
          </span>
          <span class="tooltip-value">{{ stat.value }}</span>
        </div>
        <div class="tooltip-row total">
          <span>总计</span>
          <span class="tooltip-value">{{ totalCount }}</span>
        </div>
      </div>
    </NTooltip>
  </div>
</template>

<style scoped>
.progress-cell {
  width: 100%;
  min-width: 120px;
}

.progress-wrapper {
  display: flex;
  flex-direction: column;
  gap: 6px;
  cursor: help;
}

.progress-stats {
  display: flex;
  align-items: center;
  gap: 10px;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  font-weight: 500;
}

.tooltip-content {
  padding: 4px 0;
  min-width: 140px;
}

.tooltip-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid rgb(var(--white-rgb) / 10%);
}

.tooltip-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 0;
  font-size: 13px;
}

.tooltip-row.total {
  padding-top: 6px;
  margin-top: 4px;
  border-top: 1px solid rgb(var(--white-rgb) / 10%);
  font-weight: 600;
}

.tooltip-value {
  font-weight: 600;
  font-family: 'SF Mono', Monaco, monospace;
}

.tooltip-divider {
  height: 1px;
  background: rgb(var(--white-rgb) / 10%);
  margin: 6px 0;
}

:deep(.n-progress) {
  width: 100%;
}

:deep(.n-progress-graph-line-fill) {
  transition: width 0.3s ease;
}
</style>
