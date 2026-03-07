---
applyTo: "src/**/*.ts,src/**/*.tsx,src/**/*.js,src/**/*.jsx,src/**/*.scss,src/**/*.css"
---

# Frontend instructions

This area contains React UI code for a multi-tenant agent builder built with React Flow.

- Prefer TypeScript-friendly patterns. If a file is JavaScript, add clear JSDoc for exported functions and complex props.
- Use functional components only. One component should have one clear responsibility.
- Extract repeated behavior into hooks, utilities, selectors, or feature services instead of duplicating logic across screens or nodes.
- Keep render functions declarative. Move data shaping, graph transforms, serialization, and validation out of JSX.
- Avoid `useEffect` for derived state. Compute derived values in render, `useMemo`, or selectors.
- Keep local state minimal. Put only truly interactive UI state in components; move durable builder state into the existing store/context architecture.
- Do not mutate nodes, edges, or nested builder configuration objects in place. Use immutable updates.

## React Flow rules
- Memoize custom node and edge components.
- Keep `nodeTypes`, `edgeTypes`, and event handlers stable with module scope or `useMemo`/`useCallback`.
- Avoid subscribing large components to the entire graph store when a narrow selector will do.
- Keep node data contracts explicit and stable. Validate node config before save/execute.
- Put layout, graph validation, edge rules, and serialization in pure utilities with unit tests.
- Keep drag/drop, selection, and keyboard behavior accessible and predictable.

## Styling rules
- Prefer CSS Modules or feature-scoped SCSS partials.
- Keep styles close to the component/feature they belong to.
- Reuse variables, mixins, tokens, and spacing scales. Avoid repeated magic numbers.
- Limit selector depth. Do not rely on fragile global overrides.
- Separate structure/layout styles from theme and state styles where practical.

## Testing rules
- Use Vitest + React Testing Library for components and hooks.
- Test user-visible behavior, not implementation details.
- Add unit tests for reducers, selectors, serializers, validators, and graph utilities.
- For new or changed frontend code, keep coverage at 80%+ and cognitive complexity <= 15 per function.
