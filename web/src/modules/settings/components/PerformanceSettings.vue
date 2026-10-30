<script setup lang="ts">
/**
 * 性能设置区（4 个数值项 + 保存）
 *
 * 逻辑在父组件（useSystemConfig）；纯展示 + v-model + save 事件。
 * 字段排版交给 SettingsField（桌面左标签 / 移动顶标签、控件宽度分档），
 * 说明文字从内联 span 改成字段 hint（常驻、字号与对比度达标）。
 */
import { NButton, NInputNumber } from 'naive-ui'
import SettingsSection from '@/modules/settings/components/SettingsSection.vue'
import SettingsField from '@/modules/settings/components/SettingsField.vue'

defineProps<{
  scrapeThreads: number
  taskTimeout: number
  retryCount: number
  concurrentDownloads: number
  saving: boolean
  loading?: boolean
  loadError?: string
  saveError?: string
}>()

const emit = defineEmits<{
  'update:scrapeThreads': [value: number | null]
  'update:taskTimeout': [value: number | null]
  'update:retryCount': [value: number | null]
  'update:concurrentDownloads': [value: number | null]
  save: []
  retry: []
}>()
</script>

<template>
  <SettingsSection
    title="性能"
    description="刮削与下载的并发参数，调高前先确认网络与磁盘余量"
    :error="loadError"
    :loading="loading"
    :action-error="saveError"
    @retry="emit('retry')"
  >
    <SettingsField label="刮削线程数" hint="建议 2-8，过高可能被站点限制" width="sm">
      <NInputNumber
        :value="scrapeThreads"
        :min="1"
        :max="16"
        @update:value="emit('update:scrapeThreads', $event)"
      />
    </SettingsField>

    <SettingsField label="任务超时" hint="单位秒，覆盖单个文件的整个刮削流程" width="sm">
      <NInputNumber
        :value="taskTimeout"
        :min="10"
        :max="1800"
        @update:value="emit('update:taskTimeout', $event)"
      />
    </SettingsField>

    <SettingsField label="失败重试次数" hint="网络请求失败后的重试次数" width="sm">
      <NInputNumber
        :value="retryCount"
        :min="0"
        :max="10"
        @update:value="emit('update:retryCount', $event)"
      />
    </SettingsField>

    <SettingsField label="并发下载数" hint="同时下载图片的最大数量" width="sm">
      <NInputNumber
        :value="concurrentDownloads"
        :min="1"
        :max="10"
        @update:value="emit('update:concurrentDownloads', $event)"
      />
    </SettingsField>

    <template #actions>
      <NButton type="primary" :loading="saving" @click="emit('save')">保存配置</NButton>
    </template>
  </SettingsSection>
</template>
