import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default [
  { ignores: ['dist', 'dist-verify', '.tmp-esbuild', '.tmp-ui-check', 'coverage'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    settings: { react: { version: '18.3' } },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      // This codebase is plain JSX without PropTypes; enforcing prop types
      // here only produces noise.
      'react/prop-types': 'off',
      'react/no-unescaped-entities': 'off',
      'react/display-name': 'off',
      // Local storage and similar APIs are wrapped in bare `catch {}` on
      // purpose (private browsing, quota, disabled storage).
      'no-empty': ['error', { allowEmptyCatch: true }],
      // Unused imports don't affect the bundle (tree-shaken) — surface them
      // as warnings so real errors stay actionable.
      'no-unused-vars': ['warn', { vars: 'all', args: 'none', ignoreRestSiblings: true }],
      'react/jsx-no-target-blank': 'off',
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
    },
  },
  {
    // The dragons scene is a verbatim vendor drop from the ascii-reveal demo.
    // Its unused locals (`mix`, `tintOf`, `burst`, `DOT_LV`) belong to other
    // scenes in the upstream file; pruning them would make a future re-sync
    // diff against the source noisy for no behavioural gain.
    files: ['src/vendor/**'],
    rules: { 'no-unused-vars': 'off' },
  },
]
