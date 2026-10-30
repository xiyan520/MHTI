import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import pluginVue from 'eslint-plugin-vue'

/**
 * ESLint 扁平配置（ESLint 9）。
 *
 * 分工：类型正确性交给 vue-tsc（npm run build:check），ESLint 只管「必错」规则——
 * 未使用变量、Vue 模板写法、明显的逻辑错误。不追求风格统一（那是 Prettier 的事）。
 *
 * no-undef 对 TS/Vue 关闭：类型检查器已覆盖标识符解析，开着只会把 window/document
 * 之类的浏览器全局误报成未定义。
 */
export default [
  { ignores: ['dist/**', 'node_modules/**', 'public/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: { parser: tseslint.parser },
    },
  },
  {
    files: ['**/*.{ts,vue}'],
    rules: {
      'no-undef': 'off',
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      '@typescript-eslint/no-explicit-any': 'warn',
      'vue/multi-word-component-names': 'off',
      'vue/no-v-html': 'off',
    },
  },
  {
    // 这几个「设置页签」组件的 props 是父级持有的表单草稿对象，子级就地编辑字段是
    // 既有约定（父级统一提交）。改成 defineModel/v-model 需要连父级一起重构，
    // 单独立项处理；在此之前只对这些文件放行，避免把规则整体关掉。
    files: [
      'src/modules/auth/components/LoginFormPanel.vue',
      'src/modules/scrape/components/advanced/*.vue',
    ],
    rules: { 'vue/no-mutating-props': 'off' },
  },
]
