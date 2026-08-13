from nitrostack import injectable, ExecutionContext


@injectable(deps=[])
class CLASS_NAME:
    """Authorization guard. Attach with ``@use_guards(CLASS_NAME)`` on a tool."""

    async def can_activate(self, context: ExecutionContext) -> bool:
        # TODO: implement authorization logic (scopes, roles, API keys, ...)
        if context.auth is None:
            return False
        return True
