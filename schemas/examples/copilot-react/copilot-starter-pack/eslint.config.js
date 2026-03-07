// Install dev dependencies (adjust as needed):
// npm i -D eslint @eslint/js globals typescript-eslint eslint-plugin-react-hooks eslint-plugin-react-refresh eslint-plugin-import eslint-plugin-jsx-a11y eslint-plugin-sonarjs

const js = require("@eslint/js");
const globals = require("globals");
const tseslint = require("typescript-eslint");
const reactHooks = require("eslint-plugin-react-hooks");
const reactRefresh = require("eslint-plugin-react-refresh");
const importPlugin = require("eslint-plugin-import");
const jsxA11y = require("eslint-plugin-jsx-a11y");
const sonarjs = require("eslint-plugin-sonarjs");

module.exports = [
  {
    ignores: [
      "dist/**",
      "build/**",
      "coverage/**",
      "node_modules/**",
      "*.min.js",
    ],
  },

  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,

  {
    files: ["**/*.{js,cjs,mjs,ts,tsx,jsx}"],
    languageOptions: {
      parserOptions: {
        projectService: true,
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      import: importPlugin,
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      "jsx-a11y": jsxA11y,
      sonarjs,
    },
    rules: {
      "array-callback-return": "error",
      "consistent-return": "error",
      "default-case-last": "error",
      eqeqeq: ["error", "always"],
      "max-depth": ["error", 4],
      "max-lines-per-function": ["warn", { max: 75, skipBlankLines: true, skipComments: true }],
      "max-nested-callbacks": ["error", 3],
      "max-params": ["warn", 4],
      complexity: ["warn", 10],
      "no-alert": "error",
      "no-console": ["warn", { allow: ["warn", "error"] }],
      "no-duplicate-imports": "error",
      "no-else-return": ["error", { allowElseIf: false }],
      "no-implicit-coercion": "error",
      "no-param-reassign": ["error", { props: true }],
      "no-return-await": "error",
      "no-shadow": "off",
      "@typescript-eslint/no-shadow": "error",
      "no-unneeded-ternary": "error",
      "object-shorthand": ["error", "always"],
      "prefer-const": "error",
      "prefer-template": "error",

      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/no-unnecessary-condition": "warn",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "@typescript-eslint/require-await": "error",

      "import/first": "error",
      "import/newline-after-import": "error",
      "import/no-cycle": "warn",
      "import/no-default-export": "off",
      "import/no-duplicates": "error",
      "import/order": [
        "error",
        {
          groups: [["builtin", "external"], ["internal"], ["parent", "sibling", "index"]],
          "newlines-between": "always",
          alphabetize: { order: "asc", caseInsensitive: true },
        },
      ],

      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],

      "jsx-a11y/alt-text": "error",
      "jsx-a11y/anchor-is-valid": "error",
      "jsx-a11y/click-events-have-key-events": "error",
      "jsx-a11y/no-static-element-interactions": "error",

      "sonarjs/cognitive-complexity": ["error", 15],
      "sonarjs/no-duplicated-branches": "error",
      "sonarjs/no-identical-functions": "error",
      "sonarjs/no-small-switch": "warn",
    },
  },

  {
    files: ["**/*.test.{ts,tsx,js,jsx}", "**/*.spec.{ts,tsx,js,jsx}"],
    rules: {
      "max-lines-per-function": "off",
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
];
