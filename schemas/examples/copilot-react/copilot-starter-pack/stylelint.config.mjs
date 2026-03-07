// Install dev dependencies (adjust as needed):
// npm i -D stylelint stylelint-config-standard-scss stylelint-order postcss postcss-scss

export default {
  extends: ["stylelint-config-standard-scss"],
  customSyntax: "postcss-scss",
  ignoreFiles: ["dist/**", "build/**", "coverage/**", "**/*.js", "**/*.ts", "**/*.tsx"],
  plugins: ["stylelint-order"],
  rules: {
    "alpha-value-notation": "number",
    "color-function-notation": "modern",
    "declaration-block-no-duplicate-properties": true,
    "max-nesting-depth": 3,
    "no-descending-specificity": true,
    "no-duplicate-selectors": true,
    "selector-class-pattern": [
      "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
      {
        message: "Use lowercase kebab-case for class names.",
      },
    ],
    "order/properties-alphabetical-order": true,
    "scss/at-mixin-pattern": "^[a-z][a-z0-9-]*$",
    "scss/dollar-variable-pattern": "^[a-z][a-z0-9-]*$",
  },
};
