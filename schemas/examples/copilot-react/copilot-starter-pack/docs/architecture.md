# Architecture

## Overview
The application is a multi-tenant orchestration/agent builder with a React front end and a thin Node.js backend.

## Layers

### Frontend
- **Pages / feature shells**: route-level composition
- **Feature components**: builder canvas, side panels, inspectors, catalogs, toolbars
- **Reusable primitives**: buttons, dialogs, form controls, badges, tabs
- **Hooks**: orchestration of UI behavior, async state, subscriptions
- **Domain utilities/services**: graph transforms, node validation, serialization, layout, selectors
- **API clients**: isolated transport layer for backend calls

### Backend
- **Routes / controllers**: HTTP parsing and response mapping only
- **Services**: business logic and orchestration-related use cases
- **Repositories / integration clients**: persistence and external systems
- **Validation layer**: request/response and contract schemas
- **Middleware**: auth, tenant context, correlation IDs, logging, error normalization

## Core design principles
1. Tenant context is mandatory at all data boundaries.
2. UI state and domain logic remain separate.
3. Graph transforms must be deterministic and testable.
4. Contracts between UI and backend are versioned and documented.
5. New patterns should only be introduced when existing ones are clearly insufficient.

## Frontend guidance
- Keep builder state normalized.
- Derive computed values instead of duplicating state.
- Prefer pure functions for validation, mapping, and serialization.
- Optimize React Flow usage for stable references and narrow subscriptions.

## Backend guidance
- Keep request handlers thin.
- Make validation explicit at the edges.
- Use structured logs and consistent error envelopes.
- Keep integration code isolated and replaceable.

## Recommended folders
Update to match your repo:

```text
src/
  app/
  components/
  features/
    builder/
      components/
      hooks/
      services/
      utils/
      types/
  api/
server/
  routes/
  controllers/
  services/
  repositories/
  middleware/
  validators/
docs/
```

## Definition of done
- implementation follows layering rules
- tests added/updated
- docs updated if contracts changed
- lint/build/test/Sonar pass
