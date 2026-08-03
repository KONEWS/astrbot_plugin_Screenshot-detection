import sys
import types
from unittest.mock import MagicMock

logger = MagicMock()


def _register(*args, **kwargs):
    def decorator(cls):
        return cls

    return decorator


def _filter_decorator(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


class _Context:
    def get_all_providers(self):
        return []

    async def get_current_chat_provider_id(self, umo=""):
        return "test_provider"

    async def send_message(self, *args, **kwargs):
        return True

    async def llm_generate(self, *args, **kwargs):
        resp = MagicMock()
        resp.completion_text = "测试分析结果"
        return resp


class _Star:
    def __init__(self, context):
        self.context = context


astrbot = types.ModuleType("astrbot")
astrbot_api = types.ModuleType("astrbot.api")
astrbot_api.logger = logger
astrbot_api.AstrBotConfig = MagicMock

astrbot_event = types.ModuleType("astrbot.api.event")
astrbot_event.filter = MagicMock()
astrbot_event.filter.regex = _filter_decorator
astrbot_event.filter.command = _filter_decorator
astrbot_event.AstrMessageEvent = type(
    "AstrMessageEvent",
    (),
    {"message_str": "", "unified_msg_origin": "test:GroupMessage:1"},
)


class _MessageChain:
    def __init__(self):
        self._messages = []

    def file_image(self, path):
        self._messages.append(("image", path))
        return self

    def message(self, text):
        self._messages.append(("text", text))
        return self


astrbot_event.MessageChain = _MessageChain

astrbot_star = types.ModuleType("astrbot.api.star")
astrbot_star.Context = _Context
astrbot_star.Star = _Star
astrbot_star.register = _register

astrbot.api = astrbot_api
astrbot.api.event = astrbot_event
astrbot.api.star = astrbot_star

sys.modules["astrbot"] = astrbot
sys.modules["astrbot.api"] = astrbot_api
sys.modules["astrbot.api.event"] = astrbot_event
sys.modules["astrbot.api.star"] = astrbot_star
