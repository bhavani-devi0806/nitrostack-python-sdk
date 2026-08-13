from typing import Any

from nitrostack import injectable
from nitrostack.core.pipeline import PipeMetadata


@injectable(deps=[])
class CLASS_NAME:
    """Validation/transform pipe. Attach with ``@use_pipes(CLASS_NAME)`` on a tool."""

    async def transform(self, value: Any, metadata: PipeMetadata) -> Any:
        # TODO: validate or transform ``value`` (param: metadata.param_name)
        return value
