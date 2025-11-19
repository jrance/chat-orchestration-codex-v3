# nodes.py
from langgraph.types import StreamWriter

async def my_node(state: dict, writer: StreamWriter):
    writer({
        "kind": "node.start",
        "node": "my_node",
        "details": {"foo": "bar"},
    })
    ...
    writer({
        "kind": "node.end",
        "node": "my_node",
        "details": {"status": "ok"},
    })
    return {...}
