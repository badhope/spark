// Spark Web — ESLint flat config (ESLint 9+).
// 规则取向与后端 ruff 对齐：可维护性优先、不吹毛求疵；hooks 规则完整开启。
// @ts-check
import js from "@eslint/js"
import tseslint from "typescript-eslint"
import reactHooks from "eslint-plugin-react-hooks"

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "src/spark/web/dist/**"],
  },
  js.configs.recommended,
  {
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      // Node scripts intentionally use console/process; the app sources do not.
      globals: { console: "readonly", process: "readonly" },
    },
  },
  {
    files: ["public/sw.js"],
    languageOptions: {
      globals: { self: "readonly", URL: "readonly" },
    },
  },
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // 与代码库既有风格一致：`_` 前缀参数/变量视为有意未使用
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/consistent-type-imports": "warn",
    },
  },
)
