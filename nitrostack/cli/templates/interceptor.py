from datetime import datetime, timezone
from typing import Any, Callable

from nitrostack import injectable, ExecutionContext


@injectable(deps=[])
class CLASS_NAME:
    """Execution interceptor. Attach with ``@use_interceptors(CLASS_NAME)`` on a tool."""

    async def intercept(self, context: ExecutionContext, next_fn: Callable[[], Any]) -> Any:
        result = await next_fn()
        return {
            "success": True,
            "data": result,
            "tool": context.tool_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
