# pseudo-ish compiler flow
for ir_node in ir.nodes:
    policy = resolve_effective_error_policy(ir_node, ir)
    inner_fn = build_inner_node_fn(ir_node)  # your existing compiler logic

    success_next = get_primary_success_target(ir_node)  # from your edges
    error_target = policy.on_failure.goto_node_id if policy.on_failure else None

    wrapped = wrap_node_with_error_policy(
        node_id=ir_node.id,
        node_name=ir_node.name,
        node_type=ir_node.type,
        inner_fn=inner_fn,
        policy=policy,
        success_next=success_next,
    )

    destinations = tuple(
        x for x in [success_next, error_target, "__end__"] if x
    )

    builder.add_node(
        ir_node.id,
        wrapped,
        destinations=destinations
    )

# Start node can route into your first executable node
builder.add_edge(START, first_node_id)

# Add static edges only where you really want fixed flow.
# For wrapped nodes using Command, avoid adding normal success edges from that same node.