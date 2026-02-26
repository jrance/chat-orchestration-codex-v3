from typing import TypedDict, Literal, cast
from langgraph.types import Command


# ---- shared helpers ----

def classify_exception(ex: Exception) -> ErrorCategory:
    name = ex.__class__.__name__.lower()
    msg = str(ex).lower()

    if "timeout" in name or "timeout" in msg:
        return ErrorCategory.TIMEOUT
    if "rate" in msg and "limit" in msg:
        return ErrorCategory.RATE_LIMIT
    if "auth" in msg or "permission" in msg or isinstance(ex, PermissionError):
        return ErrorCategory.AUTH
    if isinstance(ex, (ValueError, TypeError)):
        return ErrorCategory.VALIDATION
    if isinstance(ex, ConnectionError):
        return ErrorCategory.TRANSIENT
    return ErrorCategory.UNKNOWN


def exception_type_name(ex: Exception) -> str:
    return f"{ex.__class__.__module__}.{ex.__class__.__name__}"


def short_exception_name(ex: Exception) -> str:
    return ex.__class__.__name__


def compute_delay_ms(settings: RetrySettings, attempt: int) -> int:
    """attempt is 1-based; this computes delay *before next retry*."""
    base = settings.initial_delay_ms

    if settings.backoff_strategy == BackoffStrategy.FIXED:
        delay = base
    elif settings.backoff_strategy == BackoffStrategy.LINEAR:
        delay = int(base * attempt)
    else:  # exponential
        delay = int(base * (settings.backoff_factor ** (attempt - 1)))

    delay = min(delay, settings.max_delay_ms)

    if settings.jitter:
        # full jitter-ish
        delay = random.randint(int(delay * 0.5), max(delay, 1))

    return max(delay, 0)


def should_retry(ex: Exception, category: ErrorCategory, policy: ErrorPolicy) -> bool:
    full_name = exception_type_name(ex)
    short_name = short_exception_name(ex)

    if short_name in policy.classification.do_not_retry_exception_types or full_name in policy.classification.do_not_retry_exception_types:
        return False

    if category in policy.classification.retry_on_categories:
        return True

    if short_name in policy.classification.retry_on_exception_types or full_name in policy.classification.retry_on_exception_types:
        return True

    return False


def build_error_record(
    *,
    ex: Exception,
    node_id: str,
    node_name: str,
    node_type: str,
    attempt: int,
    max_attempts: int,
    is_final: bool,
    will_retry: bool,
    policy: ErrorPolicy,
    next_action: str,
    next_node_id: str | None,
    run_id: str | None,
    thread_id: str | None,
    branch_id: str | None,
    metadata: dict[str, object] | None = None,
) -> ErrorRecord:
    category = classify_exception(ex)
    stack = traceback.format_exc() if policy.telemetry.include_stack else None
    msg = "[redacted]" if policy.telemetry.redact_message else str(ex)

    return ErrorRecord(
        error_id=f"err_{uuid.uuid4().hex[:12]}",
        node_id=node_id,
        node_name=node_name,
        node_type=node_type,
        attempt=attempt,
        max_attempts=max_attempts,
        is_final=is_final,
        will_retry=will_retry,
        error_class=short_exception_name(ex),
        error_category=category.value,
        message=msg,
        stack=stack,
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        next_action=next_action,
        next_node_id=next_node_id,
        severity=policy.on_failure.severity.value,
        run_id=run_id,
        thread_id=thread_id,
        branch_id=branch_id,
        metadata=metadata or {},
    )


# ---- wrapper factory ----

def wrap_node_with_error_policy(
    *,
    node_id: str,
    node_name: str,
    node_type: str,
    inner_fn: Callable[[dict[str, object]], dict[str, object]],
    policy: ErrorPolicy,
    success_next: str,
    # all possible dynamic destinations for Command type annotation/rendering
    # for runtime this is not needed, but your compiler should know them
):
    """
    Returns a node function that:
      - executes inner_fn
      - retries according to policy
      - routes to success_next on success
      - routes/fails/continues according to on_failure on final failure
    """

    def wrapped(state: dict[str, object]) -> Command[Literal["success", "error_handler", "__end__"]]:
        # NOTE:
        # In your real compiler, generate a specific annotation like:
        # Command[Literal["actual_success_node", "actual_error_node", "__end__"]]
        # based on real destinations.

        if not policy.enabled:
            updates = inner_fn(state)
            return Command(update=updates, goto=success_next)

        start_ms = int(time.time() * 1000)
        max_attempts = max(1, policy.retry.max_attempts)

        for attempt in range(1, max_attempts + 1):
            try:
                updates = inner_fn(state)
                # Optional: emit success telemetry here
                return Command(update=updates, goto=success_next)

            except Exception as ex:
                category = classify_exception(ex)
                retriable = should_retry(ex, category, policy)
                has_attempts_left = attempt < max_attempts

                budget_exceeded = False
                if policy.retry.total_retry_budget_ms is not None:
                    elapsed = int(time.time() * 1000) - start_ms
                    budget_exceeded = elapsed >= policy.retry.total_retry_budget_ms

                will_retry = retriable and has_attempts_left and not budget_exceeded

                if will_retry:
                    record = build_error_record(
                        ex=ex,
                        node_id=node_id,
                        node_name=node_name,
                        node_type=node_type,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        is_final=False,
                        will_retry=True,
                        policy=policy,
                        next_action="retry",
                        next_node_id=node_id,
                        run_id=cast(str | None, state.get("run_id")),
                        thread_id=cast(str | None, state.get("thread_id")),
                        branch_id=cast(str | None, state.get("branch_id")),
                    )

                    delay_ms = compute_delay_ms(policy.retry, attempt)
                    # Optional: emit telemetry event here (retry scheduled)
                    time.sleep(delay_ms / 1000.0)
                    # Continue retry loop
                    continue

                # final failure path
                action = policy.on_failure.action
                goto_node = policy.on_failure.goto_node_id

                record = build_error_record(
                    ex=ex,
                    node_id=node_id,
                    node_name=node_name,
                    node_type=node_type,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    is_final=True,
                    will_retry=False,
                    policy=policy,
                    next_action=action.value,
                    next_node_id=goto_node,
                    run_id=cast(str | None, state.get("run_id")),
                    thread_id=cast(str | None, state.get("thread_id")),
                    branch_id=cast(str | None, state.get("branch_id")),
                )

                # Build common error updates
                error_updates = {
                    "last_error": record.to_state_dict(),
                    "errors": [record.to_state_dict()],  # reducer should append
                    "warnings": [record.to_state_dict()] if policy.on_failure.severity == Severity.WARNING else [],
                }

                if action == FailureActionType.GOTO_NODE and goto_node:
                    return Command(update=error_updates, goto=goto_node)

                if action == FailureActionType.CONTINUE_WITH_WARNING:
                    # Continue to the normal next node but mark warning
                    return Command(update=error_updates, goto=success_next)

                if action == FailureActionType.RETURN_DEFAULT:
                    update = dict(error_updates)
                    if isinstance(policy.on_failure.return_default, dict):
                        update.update(policy.on_failure.return_default)
                    return Command(update=update, goto=success_next)

                # FAIL_GRAPH (or missing goto target): bubble up after recording
                # We cannot "apply" updates if we raise directly, so one pattern is:
                # route to a terminal graph_error node instead of raising.
                # If you prefer raising, do it here.
                raise ex

        # unreachable
        raise RuntimeError("Retry loop exited unexpectedly")

    return wrapped