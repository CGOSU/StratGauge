import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'test-results'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser },
    },
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // 既有数据加载/表单重置 effect 广泛使用“effect 内同步 setState”模式，行为正确但被新规则反对；
      // 保持 warn 可见，待 App.tsx 按领域拆分时逐步重构，不作为 lint 失败条件。
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
)
