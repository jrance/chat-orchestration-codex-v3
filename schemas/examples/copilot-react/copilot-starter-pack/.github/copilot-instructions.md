# GitHub Copilot repository instructions

This repo is a multi-tenant web application with a React front end and a thin Node.js backend. The core product is a codeless drag-and-drop agent/orchestration builder built with React Flow.

## Architecture rules
- Keep the backend thin: route -> controller -> service -> repository/integration. Do not place business logic in Express/Fastify handlers.
- Keep the frontend modular: pages compose feature components; components compose smaller primitives; shared logic belongs in hooks, utilities, or services.
- Prefer small, focused files and reusable modules over copy/paste.
- Preserve clear separation between UI, domain logic, transport/API logic, and persistence/integration logic.

## Multi-tenant and security rules
- Every backend data access path must enforce tenant scope. Never mix or cache data across tenants.
- Validate and sanitize all client input. Treat builder JSON, node configuration, and imported graph data as untrusted.
- Never hardcode secrets, tokens, API keys, tenant IDs, or environment-specific URLs.
- Do not log secrets, access tokens, raw credentials, or unnecessary PII.

## Quality gates
- All new or changed code must include automated tests.
- Target at least 80% coverage for new or changed code.
- Keep cognitive complexity at or below 15 per function/method.
- Prefer early returns, guard clauses, extraction of helpers, and flatter control flow over deep nesting.
- Avoid duplication; extract shared behavior into utilities, hooks, services, or shared components.
- Leave files cleaner than you found them: improve names, remove dead code, and tighten types.

## Frontend expectations
- Use functional React components and hooks.
- Keep state minimal and normalized; derive values instead of duplicating state.
- Prefer pure utilities/selectors for graph transforms, validation, layout, and serialization logic.
- For React Flow, avoid broad subscriptions that re-render the whole canvas; memoize node/edge components and keep graph updates immutable.
- Styling must be clean and scoped. Prefer CSS Modules or feature-scoped SCSS. Avoid large global stylesheets and one-off magic values.

## Backend expectations
- Validate request payloads with a schema library.
- Return consistent error shapes and HTTP status codes.
- Use structured logging with correlation IDs and tenant context.
- Wrap external calls with timeouts and explicit error handling.

## Copilot output expectations
When generating code:
1. Follow the closest path-specific instruction file and AGENTS.md.
2. Prefer edits that fit the existing architecture instead of introducing a new pattern.
3. Include or update tests with the code change.
4. Add brief documentation comments only where they improve maintainability.
5. Do not generate placeholder TODO logic for core behavior unless explicitly asked.
