import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config([
  { ignores: ['dist', 'node_modules', '**/*.config.js', '**/*.config.ts'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // Keep the Hooks gate stable across plugin releases. React Compiler rules
      // require a dedicated migration and are not part of this application's build.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
        },
      ],
      // React 19 移除了 ReactDOM.render，而 antd v5 的静态方法内部正是用它渲染。
      // 于是 `Modal.confirm()` / `message.success()` 这类调用在运行时是**静默空操作**：
      // 不弹窗、不提示、不进 console、不发请求，浏览器侧完全看不出来。改用
      // `App.useApp()` 的实例，或 `@/hooks/useMessage` 里桥接好的
      // globalMessage / globalNotification / globalModal / showNotification。
      // `message` / `notification` 只有静态用法，直接禁掉这两个具名导入；
      // `Modal` 还要用作 JSX 组件不能禁，改为只拦它的静态方法调用——`App.useApp()`
      // 解构出来的是小写 `modal`，不会误伤。
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: 'antd',
              importNames: ['message', 'notification'],
              message: 'antd 静态 message/notification 在 React 19 下是空操作，请用 @/hooks/useMessage 或 App.useApp()',
            },
          ],
        },
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "CallExpression[callee.object.name='Modal'][callee.property.name=/^(confirm|info|success|error|warning|warn)$/]",
          message: 'antd 静态 Modal.confirm 在 React 19 下是空操作，请改用 globalModal 或 App.useApp().modal',
        },
      ],
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'prefer-const': 'warn',
      'no-useless-escape': 'warn',
      'no-case-declarations': 'warn',
      'no-useless-catch': 'warn',
      'no-empty': 'warn',
    },
  },
])
