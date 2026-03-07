import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx,js,jsx}", "server/**/*.{ts,js}", "backend/**/*.{ts,js}"],
      exclude: [
        "**/*.d.ts",
        "**/*.test.*",
        "**/*.spec.*",
        "**/index.*",
        "dist/**",
        "build/**",
        "coverage/**",
      ],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 80,
        statements: 80,
      },
    },
  },
});
