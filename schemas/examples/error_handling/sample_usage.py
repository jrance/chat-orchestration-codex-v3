from __future__ import annotations

from typing import TypedDict, Annotated, Literal
import operator
import random

from langgraph.graph import StateGraph, START, END
from langgraph.types import Command

# Reuse the wrapper/types from the previous section
# (assume they are imported here)


# ---------- Graph State ----------

class AppState(TypedDict, total=False):
    run_id: str
    thread_id: str
    input_text: str
    result: str
    attempts_seen: int
    continue_after_error: bool

    # error channels
    errors: Annotated[list[dict], operator.add]
    warnings: Annotated[list[dict], operator.add]
    last_error: dict


# ---------- Simulated inner node (fragile tool) ----------

def fragile_tool_inner(state: dict[str, object]) -> dict[str, object]:
    """
    Simulates a flaky operation:
    - fails while attempts_seen < 2
    - succeeds afterward
    """
    attempts_seen = int(state.get("attempts_seen", 0))
    attempts_seen += 1

    # Fail first two times
    if attempts_seen < 3:
        raise TimeoutError(f"Simulated timeout on attempt {attempts_seen}")

    return {
        "attempts_seen": attempts_seen,
        "result": f"Processed: {state.get('input_text', '')}"
    }


# ---------- Error handler node ----------

def error_handler(state: AppState) -> Command[Literal["done", "__end__"]]:
    """
    Central error handler. Could inspect state['last_error'] and decide:
    - continue to done
    - end graph
    """
    last_error = state.get("last_error", {})
    continue_after_error = bool(state.get("continue_after_error", False))

    updates = {
        "result": state.get("result") or f"Recovered from error: {last_error.get('message', 'unknown')}"
    }

    if continue_after_error:
        return Command(update=updates, goto="done")
    return Command(update=updates, goto=END)  # END is acceptable as goto target in typed annotation as "__end__"


# ---------- Done node ----------

def done_node(state: AppState) -> AppState:
    return {"result": state.get("result", "done")}


# ---------- Build policies ----------

policy_retry_then_error = ErrorPolicy(
    id="fragile-tool-policy",
    classification=ClassificationSettings(
        retry_on_categories=[ErrorCategory.TIMEOUT, ErrorCategory.TRANSIENT],
        retry_on_exception_types=["TimeoutError"],
        do_not_retry_exception_types=[],
    ),
    retry=RetrySettings(
        max_attempts=3,
        initial_delay_ms=200,
        backoff_strategy=BackoffStrategy.EXPONENTIAL,
        backoff_factor=2.0,
        max_delay_ms=1000,
        jitter=True,
        total_retry_budget_ms=10_000,
    ),
    on_failure=NodeFailureAction(
        action=FailureActionType.GOTO_NODE,
        severity=Severity.ERROR,
        continue_graph=True,
        goto_node_id="error_handler",
    ),
    telemetry=TelemetrySettings(
        emit_events=False,   # set True if you wire StreamWriter/SSE hooks
        include_stack=False,
        redact_message=False,
    ),
    side_effects=SideEffectSettings(
        idempotent=True,
        side_effect_level="external_read",
    ),
)


# ---------- Wrap the fragile node ----------

# NOTE: In a real compiler, the return type annotation on the wrapped node would be generated with
# actual destination literals, e.g. Command[Literal["done", "error_handler", "__end__"]].
wrapped_fragile = wrap_node_with_error_policy(
    node_id="fragile_tool",
    node_name="Fragile Tool",
    node_type="tool",
    inner_fn=fragile_tool_inner,
    policy=policy_retry_then_error,
    success_next="done",
)


# ---------- Build graph ----------

builder = StateGraph(AppState)

builder.add_node(
    "fragile_tool",
    wrapped_fragile,
    destinations=("done", "error_handler", "__end__"),  # render hint for Command routes
)

builder.add_node(
    "error_handler",
    error_handler,
    destinations=("done", "__end__"),
)

builder.add_node("done", done_node)

builder.add_edge(START, "fragile_tool")
builder.add_edge("done", END)

graph = builder.compile()


# ---------- Example invocations ----------

if __name__ == "__main__":
    # Case 1: fragile tool succeeds after retries
    out1 = graph.invoke({
        "run_id": "run_001",
        "thread_id": "thread_001",
        "input_text": "hello",
        "attempts_seen": 0,
        "continue_after_error": True,
        "errors": [],
        "warnings": [],
    })
    print("\nCASE 1 (succeeds after retries)")
    print(out1)

    # Case 2: force a non-retriable failure by changing policy or inner fn
    # For demo, here's a separate inner function:
    def fatal_inner(state: dict[str, object]) -> dict[str, object]:
        raise ValueError("Bad request payload")

    fatal_policy = ErrorPolicy(
        id="fatal-policy",
        classification=ClassificationSettings(
            retry_on_categories=[ErrorCategory.TIMEOUT],
            retry_on_exception_types=[],
            do_not_retry_exception_types=["ValueError"],
        ),
        retry=RetrySettings(max_attempts=3),
        on_failure=NodeFailureAction(
            action=FailureActionType.GOTO_NODE,
            severity=Severity.WARNING,
            continue_graph=True,
            goto_node_id="error_handler",
        ),
    )

    fatal_wrapped = wrap_node_with_error_policy(
        node_id="fragile_tool",
        node_name="Fragile Tool",
        node_type="tool",
        inner_fn=fatal_inner,
        policy=fatal_policy,
        success_next="done",
    )

    builder2 = StateGraph(AppState)
    builder2.add_node("fragile_tool", fatal_wrapped, destinations=("done", "error_handler", "__end__"))
    builder2.add_node("error_handler", error_handler, destinations=("done", "__end__"))
    builder2.add_node("done", done_node)
    builder2.add_edge(START, "fragile_tool")
    builder2.add_edge("done", END)
    graph2 = builder2.compile()

    out2 = graph2.invoke({
        "run_id": "run_002",
        "thread_id": "thread_002",
        "input_text": "hello",
        "attempts_seen": 0,
        "continue_after_error": True,
        "errors": [],
        "warnings": [],
    })
    print("\nCASE 2 (non-retriable -> error_handler)")
    print(out2)