class ParallelNode(Node):
    """
    Fan-out: creates N child "row states" and schedules them to the next node.
    It does NOT execute downstream nodes itself; it just creates lanes.
    """

    def __init__(
        self,
        node_id: str,
        items_path: str,
        child_start_node_id: str,
        reducer_node_id: str,
        concurrency: int,
        join_key: str = "_join",
    ) -> None:
        super().__init__(node_id)
        self.items_path = items_path
        self.child_start_node_id = child_start_node_id
        self.reducer_node_id = reducer_node_id
        self.concurrency = concurrency
        self.join_key = join_key

    async def run(self, state: OrchestrationState, ctx: ExecContext) -> ExecResult:
        items = get_path(state, self.items_path)
        if items is None:
            items = []
        if not isinstance(items, list):
            raise TypeError(f"ParallelNode items_path '{self.items_path}' must resolve to a list")

        expected = len(items)
        if expected == 0:
            # Nothing to do; just continue to reducer (which should handle empty groups if needed),
            # or skip directly to next step in your graph. Here we route to reducer for consistency.
            empty_group_id = await ctx.join_manager.create_group(expected=0, parent_state=state)
            # schedule reducer with a special "synthetic child" that causes completion
            synthetic_child = clone_for_child(state, row_value={"_empty": True})
            synthetic_child["variables"][self.join_key] = {
                "group_id": empty_group_id,
                "reducer_node_id": self.reducer_node_id,
                "expected": 0,
            }
            return ExecResult(next=[(self.reducer_node_id, synthetic_child)])

        group_id = await ctx.join_manager.create_group(expected=expected, parent_state=state)

        work: List[Tuple[str, OrchestrationState]] = []
        for idx, item in enumerate(items):
            child = clone_for_child(state, row_value=item)
            # join metadata travels with the row state
            child["variables"][self.join_key] = {
                "group_id": group_id,
                "reducer_node_id": self.reducer_node_id,
                "expected": expected,
                "child_index": idx,
            }
            # schedule the child lane at the first node after parallel
            work.append((self.child_start_node_id, child))

        # IMPORTANT: The executor will enforce concurrency with ctx.semaphore.
        # This node just enqueues tasks.
        return ExecResult(next=work)
