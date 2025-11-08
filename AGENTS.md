# AGENTS - Principles & Conventions (Seed)

- **Versioned APIs**: `/v1/*` only.
- **Configuration**: `.env` for local dev; overridable in tests.
- **Extensibility**: Node/Agent/Tool registries added in future PRs.
- **Performance**: Async everywhere; bounded concurrency to be introduced.
- **Testing**: Each PR maintains >=80% coverage for new code.

## Prompt Tokens

- Default tokens: `{tenant_id}`, `{correlation_id}`, `{request_id}`, `{now_iso}`, `{date_yyyy_mm_dd}`, `{user_details}` (JSON encoded).
- Unknown tokens render literally; register new tokens with `app.context.tokens.register_token`.

## Prompt Assembly Order

1. Organization preamble (when `context.injectOrgPreamble` is true and text is configured).
2. Agent style guide (`data.styleGuide`).
3. Expanded system instructions (`data.systemInstructions` with tokens).
4. Context variables (`data.context.vars` rendered as JSON).

## Compiler Scaffolding

- `app/compiler/builder.py` converts normalized IR plans into LangGraph `StateGraph` apps and wires default fallbacks to `END`.
- Node compilers live in `app/compiler/nodes/*` and register themselves via `app.compiler.nodes.register`. Call `ensure_builtin_compilers()` before compiling to load core kinds.
- Add new orchestration kinds by dropping a module under `app/compiler/nodes` and registering a compiler that uses the builder helpers (`add_node`, `register_conditional`).
- Compiled graphs are cached in-memory with `app/compiler/registry.py`; `/v1/compile` returns the `graph_id` for later execution flows.

## Runtime Persistence

- LangGraph checkpointing is resolved via `app.runtime.checkpointer.get_checkpointer()`, which defaults to the in-memory `MemorySaver`. Override `CHECKPOINTER_KIND` to swap implementations (e.g., Redis/SQL) after registering them with the factory.
- Run state persistence uses `app.runtime.state_store.get_run_state_store()`; the default `InMemoryRunStateStore` copies state and metadata for safe reuse. Provide alternate backends and expose them through the factory keyed by `RUN_STORE_KIND`.
- The runtime engine (`app.runtime.engine`) injects the active checkpointer during compilation and persists orchestrator state after each execution, enabling resume/retry without rewriting compiler or API layers.
