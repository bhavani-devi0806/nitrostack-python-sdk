from nitrostack import module, ConfigModule
from modules.pizzaz.pizzaz_module import PizzazModule
from health.system_health import SystemHealthCheck

@module(
    name="app",
    imports=[
        ConfigModule.for_root(env_file_path=".env", defaults={"PORT": "3000"}),
        PizzazModule
    ],
    providers=[SystemHealthCheck]
)
class AppModule:
    pass
