import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Dashboard v2 security (TDR-4 + security M-2): dashboard renders
      // user-supplied text (events.final_output, bus_messages.content,
      // session sender names). React's default text interpolation is safe;
      // dangerouslySetInnerHTML is not. Ban it project-wide — we have no
      // legitimate use case that offsets the XSS risk.
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message:
            "dangerouslySetInnerHTML is banned (Dashboard v2 security). " +
            "Use JSX text interpolation; React auto-escapes.",
        },
      ],
    },
  },
])
