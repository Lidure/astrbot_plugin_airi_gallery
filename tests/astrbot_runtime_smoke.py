from __future__ import annotations

import asyncio
import importlib.metadata
import tempfile
from pathlib import Path

from astrbot.api.star import Context, Star
from astrbot.core.agent.tool import FunctionTool

import main as plugin_module


class SmokeContext:
    def __init__(self) -> None:
        self.registered_web_apis: list[tuple[str, object, list[str], str]] = []
        self.llm_tools: list[object] = []

    def register_web_api(self, path, handler, methods, description) -> None:
        self.registered_web_apis.append((path, handler, methods, description))

    def add_llm_tools(self, tool) -> None:
        self.llm_tools.append(tool)


async def run_smoke() -> None:
    assert issubclass(plugin_module.Main, Star)
    assert issubclass(plugin_module.GalleryTool, FunctionTool)
    assert hasattr(Context, "register_web_api")

    with tempfile.TemporaryDirectory(prefix="airi-gallery-smoke-") as temp_dir:
        plugin_module.get_astrbot_plugin_data_path = lambda: temp_dir
        context = SmokeContext()
        plugin = plugin_module.Main(
            context,
            {
                "git_sync_enabled": False,
                "llm_tool_enabled": False,
            },
        )

        expected_prefix = f"/{plugin_module.PLUGIN_NAME}/"
        assert context.registered_web_apis
        assert all(path.startswith(expected_prefix) for path, *_ in context.registered_web_apis)
        assert plugin.gallery_root == Path(temp_dir) / plugin_module.PLUGIN_NAME / "gallery"

        async def no_network_startup_diagnostics() -> None:
            return None

        plugin._run_startup_diagnostics = no_network_startup_diagnostics
        await plugin.initialize()
        await asyncio.sleep(0)

        assert plugin._diagnostic_task is not None
        assert plugin.gallery_root.is_dir()

        await plugin.terminate()

        assert plugin._diagnostic_task is None
        assert plugin._shutdown_event.is_set()


if __name__ == "__main__":
    print(f"AstrBot runtime: {importlib.metadata.version('AstrBot')}")
    asyncio.run(run_smoke())
    print("Airi gallery AstrBot runtime smoke passed")
