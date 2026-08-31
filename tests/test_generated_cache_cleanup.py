import sys
import time
import types
from pathlib import Path


def _identity_decorator(*args, **kwargs):
    def decorate(function):
        return function

    return decorate


def _load_main(monkeypatch):
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    message_components = types.ModuleType("astrbot.api.message_components")
    star = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
    agent = types.ModuleType("astrbot.core.agent")
    tool = types.ModuleType("astrbot.core.agent.tool")

    class FilterStub:
        EventMessageType = types.SimpleNamespace(ALL="all")
        command = staticmethod(_identity_decorator)
        event_message_type = staticmethod(_identity_decorator)

    class StarStub:
        def __init__(self, context):
            self.context = context

    class FunctionToolStub:
        def __init__(self, **kwargs):
            pass

    api.logger = types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )
    event.AstrMessageEvent = object
    event.filter = FilterStub
    message_components.Image = object
    message_components.Reply = object
    star.Context = object
    star.Star = StarStub
    astrbot_path.get_astrbot_plugin_data_path = lambda: "plugin-data"
    tool.FunctionTool = FunctionToolStub

    astrbot.api = api
    api.event = event
    api.message_components = message_components
    api.star = star
    astrbot.core = core
    core.utils = utils
    utils.astrbot_path = astrbot_path
    core.agent = agent
    agent.tool = tool

    modules = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.message_components": message_components,
        "astrbot.api.star": star,
        "astrbot.core": core,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": astrbot_path,
        "astrbot.core.agent": agent,
        "astrbot.core.agent.tool": tool,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "main", raising=False)

    import importlib

    return importlib.import_module("main")


def _touch(path: Path, mtime: float):
    path.write_bytes(b"generated")
    path.touch()
    import os

    os.utime(path, (mtime, mtime))


def test_generated_output_cleanup_expires_old_files_and_caps_cache(
    monkeypatch, tmp_path
):
    main_module = _load_main(monkeypatch)
    prepare = getattr(main_module.Main, "_prepare_generated_output_dir", None)
    assert callable(prepare), "generated output cleanup hook is missing"

    plugin = object.__new__(main_module.Main)
    plugin.plugin_data_dir = tmp_path
    output_dir = tmp_path / "generated"
    output_dir.mkdir()

    now = time.time()
    expired = output_dir / "expired.png"
    _touch(expired, now - 25 * 60 * 60)

    for index in range(102):
        _touch(output_dir / f"old-{index:03d}.png", now - 600 - index)

    recent = output_dir / "recent.png"
    _touch(recent, now - 30)

    result = prepare(plugin)

    assert result == output_dir
    assert not expired.exists()
    assert recent.exists()
    remaining = [path for path in output_dir.iterdir() if path.is_file()]
    assert len(remaining) <= 100


def test_generated_cleanup_ignores_directories_and_symlinks(monkeypatch, tmp_path):
    main_module = _load_main(monkeypatch)
    prepare = getattr(main_module.Main, "_prepare_generated_output_dir", None)
    assert callable(prepare), "generated output cleanup hook is missing"

    plugin = object.__new__(main_module.Main)
    plugin.plugin_data_dir = tmp_path
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    nested = output_dir / "manual-dir"
    nested.mkdir()

    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    link = output_dir / "linked.png"
    try:
        link.symlink_to(outside)
    except OSError:
        link = None

    prepare(plugin)

    assert nested.exists()
    assert outside.read_bytes() == b"outside"
    if link is not None:
        assert link.is_symlink()
