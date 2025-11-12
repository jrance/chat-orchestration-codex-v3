from .entry_chat_form import build_entry_chat_form
from .router import build_router
from .sequential import build_sequential
from .parallel import build_parallel
from .reducer import build_reducer
from .publisher import build_publisher
from .output import build_output
from .agent_codeless import build_agent_codeless
from .tool import build_tool
from .mcp_server import build_mcp_server

NODE_BUILDERS = {
    "entry.chat_form": build_entry_chat_form,
    "router": build_router,
    "sequential": build_sequential,
    "parallel": build_parallel,
    "reducer": build_reducer,
    "publisher": build_publisher,
    "output": build_output,
    "agent.codeless": build_agent_codeless,
    "tool": build_tool,
    "mcpServer": build_mcp_server,
}
