import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "architecture-fixtures/**",
      "dist/**",
      "src/shared/api/generated/**",
    ],
  },
  ...tseslint.configs.recommended,
  {
    linterOptions: {
      reportUnusedDisableDirectives: "error",
    },
    rules: {
      "no-console": "error",
    },
  },
);
