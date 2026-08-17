import asyncio
import os
import sys

# Ensure parent directory is in sys.path so nitrostack can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack.testing import NitroTestingModule
from examples.calculator_server import AppModule

async def run_tests():
    print("Running NitroStack Python SDK Integration Tests...")
    
    # Initialize in-process test harness
    harness = await NitroTestingModule.create(AppModule)
    
    # 1. Test 'calculate' tool
    print("\n1. Testing 'calculate' tool...")
    res = await harness.call_tool("calculate", {
        "a": 12.5, "b": 3.5, "operation": "add"
    })
    print("Add tool result:", res)
    assert res.get("status") == "success"
    assert res.get("result") == 16.0
    
    res_div = await harness.call_tool("calculate", {
        "a": 10.0, "b": 0.0, "operation": "divide"
    })
    print("Divide by zero result:", res_div)
    assert "error" in res_div or res_div.get("status") == "failed"

    # 2. Test 'convert_temperature' tool
    print("\n2. Testing 'convert_temperature' tool...")
    res_temp = await harness.call_tool("convert_temperature", {
        "value": 100.0, "from_unit": "celsius", "to_unit": "fahrenheit"
    })
    print("Temp conversion result:", res_temp)
    assert res_temp.get("result") == 212.0

    res_wrapped = await harness.call_tool("calculate", {
        "input": {"a": 4.0, "b": 2.0, "operation": "multiply"}
    })
    print("Legacy wrapped calculate result:", res_wrapped)
    assert res_wrapped.get("status") == "success"
    assert res_wrapped.get("result") == 8.0

    # 3. Test resource templates: 'calculator://operations'
    print("\n3. Testing 'calculator://operations' resource...")
    res_ops = await harness.read_resource("calculator://operations")
    print("Operations Resource contents:", res_ops)
    assert "add" in res_ops.get("supported_operations", [])

    # 4. Test resource templates: 'calculator://results/calculator-result-1'
    print("\n4. Testing 'calculator://results/calculator-result-1' template resource...")
    res_detail = await harness.read_resource("calculator://results/calculator-result-1")
    print("Result detail Resource contents:", res_detail)
    assert "42.0" in res_detail

    # 5. Test prompt template: 'calculator_help'
    print("\n5. Testing 'calculator_help' prompt template...")
    prompt_msgs = await harness.get_prompt("calculator_help", {"operation": "add"})
    print("Prompt messages returned:")
    for msg in prompt_msgs:
        print(f"[{msg.role}]: {msg.content.text}")
    
    assert len(prompt_msgs) == 2
    assert prompt_msgs[0].role == "user"
    assert "yields 15" in prompt_msgs[1].content.text

    # 6. Test health checks resource
    print("\n6. Testing built-in 'health://status' status resource...")
    health_status = await harness.read_resource("health://status")
    print("Health checks result:", health_status)
    assert health_status.get("calculator_engine") == "healthy"

    print("\nAll integration tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_tests())
