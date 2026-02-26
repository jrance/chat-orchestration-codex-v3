from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional
import time
import random
import traceback
import uuid
from datetime import datetime, timezone


class ErrorCategory(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    AUTH = "auth"
    VALIDATION = "validation"
    PARSE = "parse"
    NOT_FOUND = "not_found"
    FATAL = "fatal"
    UNKNOWN = "unknown"


class FailureActionType(str, Enum):
    FAIL_GRAPH = "fail_graph"
    GOTO_NODE = "goto_node"
    CONTINUE_WITH_WARNING = "continue_with_warning"
    RETURN_DEFAULT = "return_default"


class Severity(str, Enum):
    WARNING = "warning"
    ERROR = "error"
    CATASTROPHIC = "catastrophic"


class BackoffStrategy(str, Enum):
    FIXED = "fixed"
    EXPONENTIAL = "exponential"
    LINEAR = "linear"


@dataclass
class RetrySettings:
    max_attempts: int = 3
    initial_delay_ms: int = 500
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    backoff_factor: float = 2.0
    max_delay_ms: int = 10_000
    jitter: bool = True
    total_retry_budget_ms: Optional[int] = None
    per_attempt_timeout_ms: Optional[int] = None  # enforce externally if needed
    respect_retry_after_header: bool = False


@dataclass
class ClassificationSettings:
    retry_on_categories: list[ErrorCategory] = field(default_factory=lambda: [
        ErrorCategory.TRANSIENT, ErrorCategory.RATE_LIMIT, ErrorCategory.TIMEOUT
    ])
    retry_on_exception_types: list[str] = field(default_factory=list)
    do_not_retry_exception_types: list[str] = field(default_factory=list)


@dataclass
class NodeFailureAction:
    action: FailureActionType = FailureActionType.FAIL_GRAPH
    severity: Severity = Severity.ERROR
    continue_graph: bool = False
    goto_node_id: Optional[str] = None
    return_default: Any = None


@dataclass
class TelemetrySettings:
    emit_events: bool = True
    include_stack: bool = False
    redact_message: bool = False


@dataclass
class SideEffectSettings:
    idempotent: bool = True
    side_effect_level: str = "none"  # none | external_read | external_write
    idempotency_key_template: Optional[str] = None


@dataclass
class ErrorPolicy:
    id: str
    enabled: bool = True
    classification: ClassificationSettings = field(default_factory=ClassificationSettings)
    retry: RetrySettings = field(default_factory=RetrySettings)
    on_failure: NodeFailureAction = field(default_factory=NodeFailureAction)
    telemetry: TelemetrySettings = field(default_factory=TelemetrySettings)
    side_effects: SideEffectSettings = field(default_factory=SideEffectSettings)


@dataclass
class ErrorRecord:
    error_id: str
    node_id: str
    node_name: str
    node_type: str
    attempt: int
    max_attempts: int
    is_final: bool
    will_retry: bool
    error_class: str
    error_category: str
    message: str
    stack: Optional[str]
    timestamp_utc: str
    next_action: str
    next_node_id: Optional[str]
    severity: str
    run_id: Optional[str] = None
    thread_id: Optional[str] = None
    branch_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_state_dict(self) -> dict[str, Any]:
        return {
            "errorId": self.error_id,
            "nodeId": self.node_id,
            "nodeName": self.node_name,
            "nodeType": self.node_type,
            "attempt": self.attempt,
            "maxAttempts": self.max_attempts,
            "isFinal": self.is_final,
            "willRetry": self.will_retry,
            "errorClass": self.error_class,
            "errorCategory": self.error_category,
            "message": self.message,
            "stack": self.stack,
            "timestampUtc": self.timestamp_utc,
            "nextAction": self.next_action,
            "nextNodeId": self.next_node_id,
            "severity": self.severity,
            "runId": self.run_id,
            "threadId": self.thread_id,
            "branchId": self.branch_id,
            "metadata": self.metadata,
        }