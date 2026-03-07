---
applyTo: "server/**/*.ts,server/**/*.js,backend/**/*.ts,backend/**/*.js,api/**/*.ts,api/**/*.js,src/server/**/*.ts,src/server/**/*.js"
---

# Backend instructions

This area contains the thin Node.js backend that supports a multi-tenant orchestration builder.

- Keep handlers/controllers thin. Put business rules in services and I/O in repositories or integration clients.
- Every service and repository path must receive and enforce tenant context. Never read or write cross-tenant data.
- Validate all request params, query strings, headers, and bodies with schemas before using them.
- Define stable request/response DTOs. Keep transport models separate from domain models when the distinction matters.
- Normalize error handling: map domain/integration failures to consistent HTTP responses. Never leak raw stack traces to clients.
- Make external calls with explicit timeouts, cancellation where supported, and narrow retry behavior only for transient failures.
- Prefer pure functions for mapping, validation, authorization checks, and orchestration payload shaping.
- Avoid hidden global state and side effects at import time.
- Use structured logs with correlation ID, tenant ID, route/action name, and safe metadata only.
- Do not log secrets, credentials, tokens, or unnecessary payload bodies.

## Maintainability rules
- Keep cognitive complexity <= 15 per function/method.
- Prefer early returns and helper extraction over nested conditionals.
- Avoid duplicated request parsing, auth checks, and response mapping; extract reusable middleware/utilities.
- Write tests for services, validators, mappers, and critical routes.
- For new or changed backend code, keep coverage at 80%+ and include negative-path tests.
