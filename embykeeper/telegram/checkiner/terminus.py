import asyncio
import random
from typing import List, Optional

import emoji
from pyrogram.errors import RPCError
from pyrogram.types import Message

from embykeeper.config import config as global_config
from embykeeper.vision import VisionSettings, recognize_image, resolve_vision_config

from . import AnswerBotCheckin


class TerminusCheckin(AnswerBotCheckin):
    name = "终点站"
    bot_username = "EmbyPublicBot"
    bot_checkin_cmd = ["/cancel", "/checkin"]
    bot_text_ignore = ["会话已取消", "没有活跃的会话"]
    bot_checked_keywords = ["今天已签到"]
    max_retries = 1
    bot_use_history = 3

    def _vision_settings(self) -> VisionSettings:
        """实时解析视觉配置, 不在类属性里缓存.

        这样面板保存后 (配置管理器会重载 config.toml) 下一次签到就能
        用上新值, 无需重启后端. 优先级: 环境变量 > 配置文件 > 默认值.
        """
        try:
            config_vision = getattr(global_config, "vision", None)
        except Exception:
            config_vision = None
        return resolve_vision_config(config_vision)

    async def _image_bytes(self, message: Message) -> Optional[bytes]:
        """下载验证码图片并返回字节, 失败返回 None."""
        try:
            image_data = await self.client.download_media(message, in_memory=True)
        except Exception as e:
            self.log.warning("下载验证码图片失败: %s", e)
            return None
        if not image_data:
            self.log.warning("验证码图片为空, 无法识别.")
            return None
        if hasattr(image_data, "getvalue"):
            return image_data.getvalue()
        return image_data

    async def on_photo(self, message: Message):
        """分析传入的验证码图片并返回验证码."""
        if not message.reply_markup:
            return

        clean = lambda o: emoji.replace_emoji(o, "").replace(" ", "")
        keys = [k for r in message.reply_markup.inline_keyboard for k in r]
        options = [k.text for k in keys]
        options_cleaned = [clean(o) for o in options]

        if len(options) < 2:
            return

        image_bytes = await self._image_bytes(message)
        if not image_bytes:
            self.log.warning("签到失败: 验证码识别错误.")
            return await self.fail()

        result = await recognize_image(
            image_bytes, options_cleaned, self._vision_settings(), log=self.log
        )
        if result:
            self.log.debug("已通过智谱解析答案: %s.", result)
        else:
            self.log.warning("签到失败: 验证码识别错误.")
            return await self.fail()

        if result not in options_cleaned:
            self.log.warning("签到失败: 返回了无效结果: %s", result)
            return await self.fail()

        result = options[options_cleaned.index(result)]

        await asyncio.sleep(random.uniform(0.5, 1.5))
        try:
            await message.click(result)
        except RPCError:
            self.log.warning("按钮点击失败.")
