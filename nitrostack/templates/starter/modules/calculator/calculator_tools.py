from nitrostack import injectable, tool, widget, ExecutionContext
from pydantic import BaseModel, Field
from typing import Literal


class CalculateInput(BaseModel):
    operation: Literal["add", "subtract", "multiply", "divide"] = Field(description="The operation to perform")
    a: float = Field(description="First number")
    b: float = Field(description="Second number")


class ConvertTemperatureInput(BaseModel):
    value: float = Field(description="Temperature value to convert")
    from_unit: Literal["celsius", "fahrenheit", "kelvin"] = Field(description="Unit to convert from")
    to_unit: Literal["celsius", "fahrenheit", "kelvin"] = Field(description="Unit to convert to")


def _to_celsius(value: float, from_unit: str) -> float:
    if from_unit == "celsius":
        return value
    if from_unit == "fahrenheit":
        return (value - 32) * 5 / 9
    return value - 273.15


def _from_celsius(celsius: float, to_unit: str) -> float:
    if to_unit == "celsius":
        return celsius
    if to_unit == "fahrenheit":
        return (celsius * 9 / 5) + 32
    return celsius + 273.15


@injectable()
class CalculatorTools:
    @tool(
        name="calculate",
        description="Perform basic arithmetic calculations",
        input_schema=CalculateInput
    )
    @widget("calculator-result")
    async def calculate(self, input: CalculateInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Performing calculation: {input.operation} on {input.a} and {input.b}")

        result = 0.0
        symbol = ""

        if input.operation == "add":
            result = input.a + input.b
            symbol = "+"
        elif input.operation == "subtract":
            result = input.a - input.b
            symbol = "-"
        elif input.operation == "multiply":
            result = input.a * input.b
            symbol = "×"
        elif input.operation == "divide":
            if input.b == 0:
                raise ValueError("Cannot divide by zero")
            result = input.a / input.b
            symbol = "÷"

        return {
            "operation": input.operation,
            "a": input.a,
            "b": input.b,
            "result": result,
            "expression": f"{input.a} {symbol} {input.b} = {result}"
        }

    @tool(
        name="convert_temperature",
        title="Convert Temperature",
        description="Convert a temperature from one unit to another (celsius, fahrenheit, kelvin)",
        input_schema=ConvertTemperatureInput
    )
    async def convert_temperature(self, input: ConvertTemperatureInput, context: ExecutionContext) -> dict:
        context.logger.info(
            f"Converting temperature: {input.value} from {input.from_unit} to {input.to_unit}"
        )
        celsius = _to_celsius(input.value, input.from_unit)
        result = round(_from_celsius(celsius, input.to_unit), 2)
        return {
            "result": result,
            "unit": input.to_unit,
            "original_value": input.value,
            "from_unit": input.from_unit,
            "to_unit": input.to_unit,
            "expression": f"{input.value}°{input.from_unit} = {result}°{input.to_unit}",
        }
