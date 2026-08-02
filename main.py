"""
截图检测插件
定时截图电脑屏幕，使用 AI 分析内容并推送到指定会话。
"""

import asyncio
import base64
import io
import os
import re
import time
from datetime import datetime

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from PIL import Image, ImageGrab

    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    logger.warning("Pillow 未安装，截图功能不可用。请运行: pip install Pillow")


@register(
    "astrbot_plugin_screenshot_detection",
    "KONEWS",
    "定时截图电脑屏幕，使用AI分析内容并推送到指定会话",
    "v1.13.0",
)
class ScreenshotDetectionPlugin(Star):
    """截图检测插件主类"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._task: asyncio.Task | None = None
        self._running: bool = False
        self._last_screenshot_time: float = 0

        # 从配置中读取设置
        self._interval: int = config.get("interval", 1200)
        self._interval = max(self._interval, 60)

        # 免打扰时段
        quiet_time_str = config.get("quiet_time", "0-8")
        self._quiet_start: int | None = None
        self._quiet_end: int | None = None
        self._parse_quiet_time(quiet_time_str)

        # 目标会话 UMO
        target_umo = config.get("target_umo", "") or None
        if target_umo and ":" not in target_umo:
            target_umo = f"default:FriendMessage:{target_umo}"
        self._target_umo: str | None = (
            self._normalize_umo(target_umo) if target_umo else None
        )

        # 自定义识图模型
        provider_id = config.get("custom_provider_id", "")
        self._custom_provider_id: str | None = provider_id if provider_id else None

        # 截图分析提示词
        self._analysis_prompt: str = config.get(
            "analysis_prompt",
            "请用你的人格设定风格，对这张电脑屏幕截图发表感想。描述你看到了什么，并用有趣的方式评论。当前时间：{{current_time}}",
        )

        # 是否发送图片
        self._send_image: bool = config.get("send_image", False)

        # 仅截屏不分析
        self._screenshot_only: bool = config.get("screenshot_only", False)

        # 截图最大尺寸
        self._image_max_size: int = config.get("image_max_size", 1280)

        # 最多保存截图张数
        self._max_screenshots: int = config.get("max_screenshots", 10)
        self._max_screenshots = max(self._max_screenshots, 1)

        # 是否自动启动
        self._auto_start: bool = config.get("auto_start", False)

        # 截图保存目录（使用 data 目录）
        self._screenshot_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data", "screenshots"
        )
        os.makedirs(self._screenshot_dir, exist_ok=True)

    def _parse_quiet_time(self, quiet_time_str: str) -> None:
        """解析免打扰时段字符串，格式：开始-结束，如 0-8"""
        if not quiet_time_str or not quiet_time_str.strip():
            self._quiet_start = 0
            self._quiet_end = 8
            return
        try:
            parts = quiet_time_str.strip().split("-")
            if len(parts) == 2:
                start = int(parts[0].strip())
                end = int(parts[1].strip())
                if 0 <= start <= 23 and 0 <= end <= 23:
                    self._quiet_start = start
                    self._quiet_end = end
        except (ValueError, IndexError):
            pass

    def _normalize_umo(self, umo: str, platform_id: str = "") -> str:
        """规范化 UMO 格式，将 2 部分转换为 3 部分"""
        parts = umo.split(":")
        if len(parts) == 3:
            return umo
        elif len(parts) == 2:
            first_part = parts[0]
            second_part = parts[1]
            if first_part in ["FriendMessage", "GroupMessage"]:
                if platform_id:
                    return f"{platform_id}:{first_part}:{second_part}"
                else:
                    return f"default:{first_part}:{second_part}"
            else:
                return f"{first_part}:FriendMessage:{second_part}"
        else:
            return umo

    def _is_quiet_time(self) -> bool:
        """检查当前是否在免打扰时段"""
        if self._quiet_start is None or self._quiet_end is None:
            return False

        current_hour = datetime.now().hour

        if self._quiet_start > self._quiet_end:
            return current_hour >= self._quiet_start or current_hour < self._quiet_end
        else:
            return self._quiet_start <= current_hour < self._quiet_end

    def _take_screenshot(self) -> bytes | None:
        """截取屏幕并返回 PNG 字节数据"""
        if not HAS_PIL:
            return None
        try:
            screenshot = ImageGrab.grab()

            max_size = self._image_max_size
            width, height = screenshot.size
            if width > max_size or height > max_size:
                ratio = min(max_size / width, max_size / height)
                new_width = int(width * ratio)
                new_height = int(height * ratio)
                screenshot = screenshot.resize(
                    (new_width, new_height), Image.Resampling.LANCZOS
                )

            buffer = io.BytesIO()
            screenshot.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None

    def _image_to_base64(self, image_bytes: bytes) -> str:
        """将图片字节转换为 base64 字符串"""
        return base64.b64encode(image_bytes).decode("utf-8")

    def _save_screenshot(self, image_bytes: bytes) -> str:
        """保存截图到本地，返回文件路径"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
        filepath = os.path.join(self._screenshot_dir, filename)

        with open(filepath, "wb") as f:
            f.write(image_bytes)

        self._cleanup_screenshots()
        return filepath

    def _cleanup_screenshots(self):
        """清理超过最大数量的旧截图"""
        try:
            files = []
            for f in os.listdir(self._screenshot_dir):
                if f.startswith("screenshot_") and f.endswith(".png"):
                    filepath = os.path.join(self._screenshot_dir, f)
                    files.append((filepath, os.path.getmtime(filepath)))

            files.sort(key=lambda x: x[1])

            while len(files) > self._max_screenshots:
                filepath, _ = files.pop(0)
                try:
                    os.remove(filepath)
                    logger.debug(f"已删除旧截图: {filepath}")
                except Exception as e:
                    logger.warning(f"删除旧截图失败: {e}")
        except Exception as e:
            logger.warning(f"清理截图失败: {e}")

    def _get_screenshot_count(self) -> int:
        """获取当前保存的截图数量"""
        try:
            count = 0
            for f in os.listdir(self._screenshot_dir):
                if f.startswith("screenshot_") and f.endswith(".png"):
                    count += 1
            return count
        except Exception:
            return 0

    async def initialize(self):
        """插件初始化，自动启动截图任务"""
        if self._auto_start and self._target_umo:
            logger.info("自动启动截图任务...")
            if not HAS_PIL:
                logger.warning("Pillow 未安装，无法启动截图任务")
                return
            self._running = True
            self._task = asyncio.create_task(self._auto_periodic_task())
            logger.info(
                f"截图任务已启动，间隔 {self._interval} 秒，目标 {self._target_umo}"
            )

    async def _auto_periodic_task(self):
        """自动定时截图任务"""
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break

                if self._is_quiet_time():
                    logger.info(
                        f"当前在免打扰时段 ({self._quiet_start}-{self._quiet_end})，跳过截图"
                    )
                    continue

                logger.info("正在截取屏幕...")
                image_bytes = self._take_screenshot()
                if not image_bytes:
                    logger.warning("截图失败")
                    continue

                self._last_screenshot_time = time.time()
                screenshot_path = self._save_screenshot(image_bytes)
                logger.info(f"截图已保存: {screenshot_path}")

                # 仅截屏模式
                if self._screenshot_only:
                    if self._send_image and self._target_umo:
                        try:
                            from astrbot.api.event import MessageChain

                            image_chain = MessageChain().file_image(screenshot_path)
                            await self.context.send_message(
                                self._target_umo, image_chain
                            )
                            logger.info("截图已发送")
                        except Exception as e:
                            logger.error(f"发送截图失败: {e}")
                    continue

                # 分析模式
                try:
                    analysis = await self._analyze_screenshot_auto(image_bytes)
                except Exception as e:
                    logger.error(f"分析截图失败: {e}")
                    analysis = f"分析失败: {e!s}"

                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                result_text = f"[{timestamp}] 屏幕分析感想:\n\n{analysis}"

                try:
                    from astrbot.api.event import MessageChain

                    if self._send_image:
                        image_chain = MessageChain().file_image(screenshot_path)
                        await self.context.send_message(self._target_umo, image_chain)
                        await asyncio.sleep(1)

                    text_chain = MessageChain().message(result_text)
                    success = await self.context.send_message(
                        self._target_umo, text_chain
                    )

                    if success:
                        logger.info("截图分析已发送")
                    else:
                        logger.error(f"发送消息失败，UMO: {self._target_umo}")
                except Exception as e:
                    logger.error(f"发送消息异常: {e}", exc_info=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时任务异常: {e}")
                await asyncio.sleep(10)

    async def _analyze_screenshot_auto(self, image_bytes: bytes) -> str:
        """自动模式下分析截图"""
        try:
            provider_id = self._custom_provider_id
            if not provider_id:
                providers = self.context.get_all_providers()
                if providers:
                    provider_id = providers[0].meta().id
                else:
                    return "错误：未配置 LLM 模型"

            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            prompt = self._analysis_prompt.replace("{{current_time}}", current_time)

            if self._send_image:
                base64_str = self._image_to_base64(image_bytes)
                image_url = f"data:image/png;base64,{base64_str}"
                llm_resp = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    image_urls=[image_url],
                )
            else:
                llm_resp = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                )

            return llm_resp.completion_text
        except Exception as e:
            logger.error(f"分析截图失败: {e}", exc_info=True)
            return f"截图分析失败: {e!s}"

    async def _get_provider_id(self, umo: str) -> str:
        """获取 LLM 提供商 ID"""
        if self._custom_provider_id:
            return self._custom_provider_id
        return await self.context.get_current_chat_provider_id(umo=umo)

    async def _analyze_screenshot(
        self, image_bytes: bytes, event: AstrMessageEvent
    ) -> str:
        """使用当前会话人格分析截图"""
        try:
            persona = None
            if event:
                try:
                    persona_id = self.context.get_curr_persona_id(
                        event.unified_msg_origin
                    )
                    if persona_id:
                        persona = self.context.get_persona(persona_id)
                except Exception:
                    pass

            provider_id = await self._get_provider_id(event.unified_msg_origin)

            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            prompt = self._analysis_prompt.replace("{{current_time}}", current_time)

            if persona:
                prompt = prompt.replace("{{persona_name}}", persona.name)
                prompt = prompt.replace("{{persona_prompt}}", persona.prompt)
            else:
                prompt = prompt.replace("{{persona_name}}", "")
                prompt = prompt.replace("{{persona_prompt}}", "")

            if self._send_image:
                base64_str = self._image_to_base64(image_bytes)
                image_url = f"data:image/png;base64,{base64_str}"
                llm_resp = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                    image_urls=[image_url],
                )
            else:
                llm_resp = await self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=prompt,
                )

            return llm_resp.completion_text
        except Exception as e:
            logger.error(f"分析截图失败: {e}", exc_info=True)
            return f"截图分析失败: {e!s}"

    def _get_target_umo(self, event: AstrMessageEvent) -> str:
        """获取目标会话 UMO"""
        if self._target_umo:
            return self._target_umo
        return event.unified_msg_origin

    def _format_interval(self, seconds: int) -> str:
        """格式化间隔时间为易读格式"""
        if seconds >= 3600 and seconds % 3600 == 0:
            return f"{seconds // 3600}小时"
        elif seconds >= 60 and seconds % 60 == 0:
            return f"{seconds // 60}分钟"
        else:
            return f"{seconds}秒"

    def _parse_interval(self, interval_str: str) -> int:
        """解析间隔时间字符串，支持 s/m/h 单位"""
        interval_str = interval_str.strip().lower()
        if interval_str.endswith("h"):
            return int(interval_str[:-1]) * 3600
        elif interval_str.endswith("m"):
            return int(interval_str[:-1]) * 60
        elif interval_str.endswith("s"):
            return int(interval_str[:-1])
        else:
            return int(interval_str)

    def _save_config(self):
        """保存配置到文件"""
        self.config["interval"] = self._interval

        if self._quiet_start is not None and self._quiet_end is not None:
            self.config["quiet_time"] = f"{self._quiet_start}-{self._quiet_end}"
        else:
            self.config["quiet_time"] = ""

        if self._target_umo:
            parts = self._target_umo.split(":")
            if len(parts) == 3:
                self.config["target_umo"] = self._target_umo
            else:
                self.config["target_umo"] = ""
        else:
            self.config["target_umo"] = ""

        if self._custom_provider_id:
            self.config["custom_provider_id"] = self._custom_provider_id
        else:
            self.config["custom_provider_id"] = ""

        self.config["analysis_prompt"] = self._analysis_prompt
        self.config["screenshot_only"] = self._screenshot_only
        self.config["send_image"] = self._send_image
        self.config["image_max_size"] = self._image_max_size
        self.config["max_screenshots"] = self._max_screenshots

        self.config.save_config()

    @filter.regex(r"(看一下|看看|查看).*(电脑|屏幕|桌面)")
    async def natural_language_screenshot(self, event: AstrMessageEvent):
        """自然语言触发截图"""
        message = event.message_str
        delay_match = re.search(r"(\d+)\s*(分钟|小时|秒)后", message)

        if delay_match:
            amount = int(delay_match.group(1))
            unit = delay_match.group(2)

            if unit == "秒":
                delay_seconds = amount
            elif unit == "分钟":
                delay_seconds = amount * 60
            elif unit == "小时":
                delay_seconds = amount * 3600
            else:
                delay_seconds = 0

            if delay_seconds > 0:
                yield event.plain_result(f"好的，{amount}{unit}后为你截图分析")
                await asyncio.sleep(delay_seconds)

        if not HAS_PIL:
            yield event.plain_result("错误: Pillow 未安装")
            return

        yield event.plain_result("正在截取屏幕...")
        image_bytes = self._take_screenshot()
        if not image_bytes:
            yield event.plain_result("截图失败")
            return

        screenshot_path = self._save_screenshot(image_bytes)

        yield event.plain_result("正在分析截图...")
        analysis = await self._analyze_screenshot(image_bytes, event)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result_text = f"[{timestamp}] 屏幕分析感想:\n\n{analysis}"

        if self._send_image:
            yield event.image_result(screenshot_path)

        yield event.plain_result(result_text)

    @filter.command("kan")
    async def screenshot_now(self, event: AstrMessageEvent):
        """立即截取屏幕并分析"""
        if not HAS_PIL:
            yield event.plain_result("错误: Pillow 未安装，请运行 `pip install Pillow`")
            return

        yield event.plain_result("正在截取屏幕...")
        image_bytes = self._take_screenshot()
        if not image_bytes:
            yield event.plain_result("截图失败")
            return

        screenshot_path = self._save_screenshot(image_bytes)

        yield event.plain_result("正在分析截图...")
        analysis = await self._analyze_screenshot(image_bytes, event)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result_text = f"[{timestamp}] 屏幕分析感想:\n\n{analysis}"

        if self._send_image:
            yield event.image_result(screenshot_path)

        yield event.plain_result(result_text)

    @filter.command("screenshot_start")
    async def start_screenshot(self, event: AstrMessageEvent):
        """开始定时截图检测"""
        if self._running:
            yield event.plain_result("截图检测已在运行中")
            return

        if not HAS_PIL:
            yield event.plain_result("错误: Pillow 未安装")
            return

        args = event.message_str.split()
        if len(args) > 1:
            try:
                self._interval = self._parse_interval(args[1])
                self._interval = max(self._interval, 60)
                self._save_config()
            except ValueError:
                pass

        self._running = True
        self._task = asyncio.create_task(self._auto_periodic_task())

        interval_text = self._format_interval(self._interval)
        quiet_info = ""
        if self._quiet_start is not None and self._quiet_end is not None:
            quiet_info = f"\n免打扰时段: {self._quiet_start}:00 - {self._quiet_end}:00"

        yield event.plain_result(
            f"截图检测已启动\n间隔时间: {interval_text}{quiet_info}"
        )

    @filter.command("screenshot_stop")
    async def stop_screenshot(self, event: AstrMessageEvent):
        """停止定时截图检测"""
        if not self._running:
            yield event.plain_result("截图检测未在运行")
            return

        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

        yield event.plain_result("截图检测已停止")

    @filter.command("screenshot_interval")
    async def set_interval(self, event: AstrMessageEvent):
        """设置截图间隔时间"""
        args = event.message_str.split()
        if len(args) < 2:
            yield event.plain_result(
                f"当前间隔: {self._format_interval(self._interval)}"
            )
            return

        try:
            new_interval = self._parse_interval(args[1])
            if new_interval < 60:
                yield event.plain_result("间隔时间不能小于60秒")
                return
            self._interval = new_interval
            self._save_config()
            yield event.plain_result(
                f"截图间隔已设置为: {self._format_interval(self._interval)}"
            )
        except ValueError:
            yield event.plain_result("无效的时间格式")

    @filter.command("screenshot_quiet")
    async def set_quiet_time(self, event: AstrMessageEvent):
        """设置免打扰时段"""
        args = event.message_str.split()

        if len(args) < 2:
            if self._quiet_start is not None and self._quiet_end is not None:
                yield event.plain_result(
                    f"当前免打扰时段: {self._quiet_start}:00 - {self._quiet_end}:00\n"
                    f"使用 /screenshot_quiet off 关闭"
                )
            else:
                yield event.plain_result("免打扰时段未设置")
            return

        if args[1].lower() == "off":
            self._quiet_start = None
            self._quiet_end = None
            self._save_config()
            yield event.plain_result("免打扰时段已关闭")
            return

        if len(args) < 3:
            yield event.plain_result("请同时指定开始和结束小时")
            return

        try:
            start = int(args[1])
            end = int(args[2])
            if not (0 <= start <= 23 and 0 <= end <= 23):
                yield event.plain_result("小时数必须在 0-23 之间")
                return
            self._quiet_start = start
            self._quiet_end = end
            self._save_config()
            yield event.plain_result(f"免打扰时段已设置: {start}:00 - {end}:00")
        except ValueError:
            yield event.plain_result("无效的小时数")

    @filter.command("screenshot_model")
    async def set_model(self, event: AstrMessageEvent):
        """设置识图模型"""
        args = event.message_str.split()

        if len(args) < 2:
            if self._custom_provider_id:
                yield event.plain_result(f"当前识图模型: {self._custom_provider_id}")
            else:
                yield event.plain_result("当前使用默认模型")
            return

        if args[1].lower() == "default":
            self._custom_provider_id = None
            self._save_config()
            yield event.plain_result("已恢复使用默认模型")
        else:
            self._custom_provider_id = args[1]
            self._save_config()
            yield event.plain_result(f"识图模型已设置为: {self._custom_provider_id}")

    @filter.command("screenshot_target")
    async def set_target(self, event: AstrMessageEvent):
        """设置目标会话 UMO"""
        args = event.message_str.split(maxsplit=1)
        platform_id = (
            event.get_platform_id() if hasattr(event, "get_platform_id") else ""
        )

        if len(args) < 2:
            if self._target_umo:
                yield event.plain_result(f"当前目标: {self._target_umo}")
            else:
                yield event.plain_result("当前目标: 默认（触发会话）")
            return

        if args[1].lower() == "default":
            self._target_umo = None
            self._save_config()
            yield event.plain_result("已恢复默认目标会话")
        else:
            umo = args[1].strip()
            normalized_umo = self._normalize_umo(umo, platform_id)
            self._target_umo = normalized_umo
            self._save_config()
            yield event.plain_result(f"目标会话已设置为: {self._target_umo}")

    @filter.command("screenshot_test")
    async def test_send(self, event: AstrMessageEvent):
        """测试发送消息到目标会话"""
        if not self._target_umo:
            yield event.plain_result("错误：未设置目标会话 UMO")
            return

        try:
            from astrbot.api.event import MessageChain

            message_chain = MessageChain().message("这是一条测试消息，来自截图检测插件")
            success = await self.context.send_message(self._target_umo, message_chain)
            if success:
                yield event.plain_result(f"测试消息已发送到: {self._target_umo}")
            else:
                yield event.plain_result("发送失败，请检查 UMO 格式")
        except Exception as e:
            yield event.plain_result(f"发送异常: {e!s}")

    @filter.command("screenshot_status")
    async def screenshot_status(self, event: AstrMessageEvent):
        """查看截图检测状态"""
        status = "运行中" if self._running else "已停止"
        last_time = (
            datetime.fromtimestamp(self._last_screenshot_time).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if self._last_screenshot_time
            else "无"
        )
        quiet_info = "未设置"
        if self._quiet_start is not None and self._quiet_end is not None:
            quiet_info = f"{self._quiet_start}:00 - {self._quiet_end}:00"

        model_info = "默认"
        if self._custom_provider_id:
            model_info = self._custom_provider_id

        target_info = "默认（触发会话）"
        if self._target_umo:
            target_info = self._target_umo

        screenshot_count = self._get_screenshot_count()
        mode_info = "仅截屏" if self._screenshot_only else "截屏+分析"

        yield event.plain_result(
            f"=== 截图检测状态 ===\n"
            f"状态: {status}\n"
            f"模式: {mode_info}\n"
            f"间隔: {self._format_interval(self._interval)}\n"
            f"免打扰: {quiet_info}\n"
            f"识图模型: {model_info}\n"
            f"目标会话: {target_info}\n"
            f"已保存截图: {screenshot_count}/{self._max_screenshots}张\n"
            f"上次截图: {last_time}"
        )

    async def terminate(self):
        """插件销毁"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
