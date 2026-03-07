# Node and Edge Contracts

## Purpose
This document defines the minimum expectations for node and edge models used by the visual builder and backend APIs.

## Node contract expectations
Each node should have:
- stable `id`
- `type` from a documented catalog
- position/layout metadata required by the UI
- typed configuration payload
- validation status and user-facing validation messages when relevant
- explicit input/output expectations when the node participates in orchestration flow
- migration/versioning strategy if persisted over time

## Edge contract expectations
Each edge should have:
- stable `id`
- `source` and `target`
- source/target handle identifiers when applicable
- validation rules for allowed connections
- optional metadata for labels, conditions, styling, or execution semantics

## Rules
1. Node and edge IDs must be stable and unique.
2. Persisted graph shapes must be serializable without runtime-only UI artifacts unless explicitly allowed.
3. Validation logic should live in pure functions that are shared where practical.
4. UI rendering should consume contracts, not invent them.
5. Backend APIs must validate incoming graph payloads rather than trusting the client.

## Versioning
- Add a schema/version field to persisted graph payloads.
- Provide migration functions for breaking contract changes.
- Document changes to node config shape and edge semantics.

## Testing expectations
- unit tests for validators, serializers, deserializers, and migration functions
- fixture-based tests for representative graphs
- negative-path tests for invalid node config and invalid edge wiring

## Suggested model shape
```ts
type BuilderNode<TConfig = unknown> = {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: {
    label?: string;
    config: TConfig;
    version: number;
    validation?: {
      isValid: boolean;
      messages: string[];
    };
  };
};

type BuilderEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
  data?: {
    label?: string;
    condition?: string;
    version?: number;
  };
};
```
