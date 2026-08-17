import asyncio
import os
import sys

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import injectable, tool, widget, module, ExecutionContext
from nitrostack.core.decorators import widget_resource_uri
from nitrostack.testing import NitroTestingModule
from pydantic import BaseModel
import mcp.types as types

class DummyInput(BaseModel):
    pass

@injectable()
class WidgetController:
    @tool(
        name="widget_tool",
        description="A tool with a widget",
        input_schema=DummyInput
    )
    @widget("my-custom-widget-route")
    async def widget_tool(self, input: DummyInput, context: ExecutionContext) -> dict:
        return {"status": "ok"}

@module(
    name="widget_test",
    controllers=[WidgetController]
)
class WidgetTestModule:
    pass

async def main():
    print("Testing widget decorator metadata mapping...")
    
    # 1. Initialize test harness
    harness = await NitroTestingModule.create(WidgetTestModule)
    
    # 2. Extract tools by invoking the registered `tools/list` handler directly
    #    (the owned low-level Server has no FastMCP-style `list_tools()` convenience method)
    list_tools_handler = harness.app.mcp_server.request_handlers[types.ListToolsRequest]
    list_result = await list_tools_handler(None)
    tools = list_result.root.tools

    # Find our tool
    target_tool = None
    for t in tools:
        if t.name == "widget_tool":
            target_tool = t
            break
            
    assert target_tool is not None, "widget_tool was not registered"
    
    print("Registered tool representation:", target_tool)
    
    # Verify metadata fields are present
    meta = getattr(target_tool, "meta", None)
    if meta is None:
        meta = getattr(target_tool, "_meta", {})
        
    assert meta is not None, "Tool metadata is missing"
    print("Tool metadata:", meta)
    
    # Check that widget fields are populated in metadata
    assert meta.get("ui/template") == "ui://widget/my-custom-widget-route.html"
    assert meta.get("openai/outputTemplate") == "ui://widget/my-custom-widget-route.html"
    assert meta.get("ui") == {"resourceUri": "ui://widget/my-custom-widget-route.html"}
    
    print("Success! Widget metadata is correctly mapped and verified in the MCP Tool specification.")


def test_widget_metadata_on_listed_tool():
    asyncio.run(main())


def test_widget_resource_uri_normalization():
    assert widget_resource_uri("calculator-result") == "ui://widget/calculator-result.html"
    assert widget_resource_uri("ui://widget/pizza-map.html") == "ui://widget/pizza-map.html"
    try:
        widget_resource_uri("")
        raise AssertionError("empty route should raise")
    except ValueError:
        pass


if __name__ == "__main__":
    test_widget_resource_uri_normalization()
    asyncio.run(main())
