@dataclass
class JoinGroup:
    group_id: str
    expected: int
    parent_state: OrchestrationState
    # collected children states that reached reducer
    children: List[OrchestrationState] = field(default_factory=list)
    # once reduced, prevent double-reduce
    reduced: bool = False


class JoinManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._groups: Dict[str, JoinGroup] = {}

    async def create_group(self, expected: int, parent_state: OrchestrationState) -> str:
        group_id = str(uuid.uuid4())
        async with self._lock:
            self._groups[group_id] = JoinGroup(
                group_id=group_id,
                expected=expected,
                parent_state=parent_state,
            )
        return group_id

    async def add_child(self, group_id: str, child_state: OrchestrationState) -> Tuple[bool, Optional[JoinGroup]]:
        """
        Returns (is_complete, group_if_complete).
        """
        async with self._lock:
            group = self._groups.get(group_id)
            if not group:
                raise KeyError(f"Join group not found: {group_id}")

            group.children.append(child_state)
            is_complete = len(group.children) >= group.expected

            # Only return group if it is complete and not already reduced
            if is_complete and not group.reduced:
                return True, group

            return False, None

    async def mark_reduced(self, group_id: str) -> None:
        async with self._lock:
            group = self._groups.get(group_id)
            if not group:
                return
            group.reduced = True

    async def delete_group(self, group_id: str) -> None:
        async with self._lock:
            self._groups.pop(group_id, None)
