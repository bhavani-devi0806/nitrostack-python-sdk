from nitrostack import module, ConfigModule
from modules.calculator.calculator_module import CalculatorModule
from health.system_health import SystemHealthCheck

@module(
    name="app",
    imports=[
        ConfigModule.for_root(env_file_path=".env", defaults={"PORT": "3000"}),
        CalculatorModule
    ],
    providers=[SystemHealthCheck]
)
class AppModule:
    pass
