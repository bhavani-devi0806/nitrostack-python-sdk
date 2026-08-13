from datetime import datetime, timezone
from typing import Any

from nitrostack import injectable, ExecutionContext


@injectable(deps=[])
class CLASS_NAME:
    """Exception filter. Attach with ``@use_filters(CLASS_NAME)`` on a tool."""

    async def catch(self, error: Exception, context: ExecutionContext) -> Any:
        context.logger.error(f"{type(error).__name__}: {error}")
        return {
            "statusCode": getattr(error, "status", 500),
            "error": type(error).__name__,
            "message": str(error) or "Internal server error",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": context.tool_name,
        }
