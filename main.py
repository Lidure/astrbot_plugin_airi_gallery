from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


class Main(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    @filter.command("airi_gallery")
    async def airi_gallery(self, event: AstrMessageEvent):
        """Airi gallery plugin scaffold."""
        yield event.plain_result("astrbot_plugin_airi_gallery 已加载。")

    async def terminate(self):
        """Called when the plugin is unloaded or disabled."""
