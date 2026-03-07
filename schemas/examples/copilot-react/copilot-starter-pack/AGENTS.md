# AGENTS.md

## Repo purpose
This repository contains a multi-tenant agent/orchestration builder with:
- a React front end for visual graph editing using React Flow
- a thin Node.js backend for auth, persistence, validation, and orchestration-related APIs

## Core domain concepts
- **Tenant**: the primary security and data isolation boundary
- **Builder graph**: nodes, edges, layout metadata, UI state, validation status, and serialized orchestration config
- **Node contract**: typed configuration plus input/output expectations
- **Execution contract**: the serialized representation sent to backend/runtime services

## Non-negotiable invariants
1. Never break tenant isolation.
2. Never silently mutate graph state in place.
3. Node config, graph serialization, and validation rules must stay consistent across UI and backend.
4. Prefer existing patterns over introducing a second architecture for the same concern.
5. New code must include tests and must satisfy repo quality gates.

## Frontend working model
- Keep components small and composable.
- Put graph transforms, validation, serialization, and layout in pure utilities or feature services.
- Use hooks for behavior orchestration, not for hiding large amounts of business logic.
- Keep React Flow performance in mind: narrow subscriptions, stable callbacks, memoized node/edge components.

## Backend working model
- Handlers/controllers stay thin.
- Services hold business rules.
- Repositories/integration clients isolate persistence and external calls.
- Validate input at boundaries and return consistent error shapes.

## Preferred change strategy
When implementing a feature:
1. Identify the feature area and reuse the closest existing patterns.
2. Update contracts/types first.
3. Implement logic in small units.
4. Add or update tests at the same time.
5. Update docs when contracts or architecture assumptions change.

## Quality gates
- Coverage on new or changed code: 80%+
- Cognitive complexity per function/method: <= 15
- Keep duplication low; extract shared code instead of copy/paste
- Prioritize readability, explicit naming, and deterministic behavior

## Things to avoid
- large monolithic React components
- route handlers with embedded business logic
- giant SCSS files with broad global selectors
- hidden tenant assumptions
- ad hoc graph object shapes not backed by documented contracts
