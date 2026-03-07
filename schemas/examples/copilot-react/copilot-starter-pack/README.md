# Multi-Tenant Agent Builder Starter Standards

This starter pack adds repository guidance and quality configuration for a React + Node.js multi-tenant orchestration builder.

## What is included
- `.github/copilot-instructions.md` - repository-wide Copilot rules
- `.github/instructions/*.instructions.md` - path-specific frontend/backend guidance
- `AGENTS.md` - domain and architecture guardrails for AI agents
- `docs/architecture.md` - high-level architecture and layering
- `docs/node-and-edge-contracts.md` - graph contract guidance
- `eslint.config.js` - lint rules for maintainability and complexity
- `stylelint.config.mjs` - SCSS/CSS quality rules
- `vitest.config.ts` - test + coverage baseline
- `sonar-project.properties` - Sonar scanner defaults
- `.github/CODEOWNERS` - ownership template
- `.github/pull_request_template.md` - PR checklist

## Important follow-up steps
1. Adjust the `applyTo` globs in `.github/instructions/*.instructions.md` to match your repo layout exactly.
2. Install the dev dependencies referenced by `eslint.config.js` and `stylelint.config.mjs`.
3. Wire CI to run lint, tests, coverage, and Sonar scanning on every pull request.
4. Update the docs with your real folder structure, API names, and graph schema details.
5. Configure the Sonar quality gate/server profile to enforce your required thresholds on **new code**.

## Suggested CI checks
- lint
- typecheck
- unit/integration tests
- coverage report
- Sonar analysis
- build

## Standard quality bar
- coverage on new/changed code >= 80%
- cognitive complexity <= 15 per function/method
- low duplication
- no blocker/critical issues on new code
- all tenant-sensitive paths reviewed for isolation and auth
