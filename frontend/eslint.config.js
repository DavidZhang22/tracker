import js from "@eslint/js";
import globals from "globals";
import hooks from "eslint-plugin-react-hooks";

export default [
  {
    ignores: ["build/**", "coverage/**", ".mobile-check/**", "node_modules/**"],
  },
  js.configs.recommended,
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: globals.browser,
    },
    plugins: { "react-hooks": hooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      "no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          caughtErrors: "none",
          ignoreRestSiblings: true,
        },
      ],
    },
  },
  {
    files: ["src/__tests__/**/*.{js,jsx}", "src/setupTests.js"],
    languageOptions: {
      globals: {
        ...globals.node,
        ...Object.fromEntries(
          [
            "test",
            "it",
            "expect",
            "describe",
            "beforeEach",
            "afterEach",
            "beforeAll",
            "afterAll",
          ].map((name) => [name, "readonly"]),
        ),
      },
    },
  },
  {
    files: ["*.config.js", "scripts/*.{cjs,mjs}"],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["scripts/mobile-check.cjs"],
    languageOptions: { globals: globals.browser },
  },
];
