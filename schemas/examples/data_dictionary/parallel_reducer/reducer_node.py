class ReducerNode(Node):
    def __init__(
        self,
        node_id: str,
        next_node_id: str,
        result_list_key: str,
        join_key: str = "_join",
        merge_child_variables: bool = False,
        child_variables_key: str = "child_variables",
        merge_messages: bool = False,
    ) -> None:
        super().__init__(node_id)
        self.next_node_id = next_node_id
        self.result_list_key = result_list_key
        self.join_key = join_key
        self.merge_child_variables = merge_child_variables
        self.child_variables_key = child_variables_key
        self.merge_messages = merge_messages

    async def run(self, state: OrchestrationState, ctx: ExecContext) -> ExecResult:
        join_info = ensure_dict(state.get("variables", {})).get(self.join_key)
        if not isinstance(join_info, dict) or "group_id" not in join_info:
            raise KeyError(f"ReducerNode expected variables['{self.join_key}']['group_id']")

        group_id = join_info["group_id"]

        complete, group = await ctx.join_manager.add_child(group_id, state)
        if not complete or group is None:
            # Not all children have arrived yet. This lane stops here.
            return ExecResult(next=[])

        # Prevent double-reduce if multiple "last children" race
        await ctx.join_manager.mark_reduced(group_id)

        parent = group.parent_state
        parent_vars = ensure_dict(parent.get("variables"))
        parent_vars.setdefault(self.result_list_key, [])

        # Merge each child row back into parent variables[result_list_key]
        for child in group.children:
            child_row = ensure_dict(child.get("row"))
            parent_vars[self.result_list_key].append(child_row)

            if self.merge_child_variables:
                parent_vars.setdefault(self.child_variables_key, [])
                parent_vars[self.child_variables_key].append(ensure_dict(child.get("variables")))

            if self.merge_messages:
                parent.setdefault("messages", [])
                parent["messages"].extend(child.get("messages", []))

        parent["variables"] = parent_vars

        # cleanup join group to avoid memory growth
        await ctx.join_manager.delete_group(group_id)

        # Continue the single parent lane after reducer
        return ExecResult(next=[(self.next_node_id, parent)])
