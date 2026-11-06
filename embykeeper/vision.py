"""视觉识别 (验证码图片) 的配置解析与调用.

仅「终点站」签到器使用. 配置来源按优先级排列:

1. 环境变量 (兼容旧行为, 供命令行 / Docker Compose 用户使用):
   ``VISION_BASE_URL`` / ``ZHIPU_API_KEY`` / ``ZHIPU_VISION_MODEL``
2. 配置文件 ``[vision]`` 一节 (面板「系统设置 - 视觉识别」写入, 见
   ``embykeeper.schema.VisionConfig`` 与 ``embykeeper.api.config``).
3. 内置默认值 (见下方常量, 含原先硬编码的共享 Key).

``base_url`` 统一理解为 OpenAI 兼容的 API 根地址, **不含**
``/chat/completions`` 后缀. 旧版把完整端点 (含 ``/chat/completions``)
存在 ``VISION_BASE_URL`` 里, 这里在归一化时会自动去掉该后缀, 因此新旧
写法都能用, 调用时再统一拼上 ``/chat/completions``.

识别函数 :func:`recognize_image` 的逻辑照抄用户版 terminus.py 的
``_zhipu_visual``: 最多重试 3 次, 每次重试前等 3 秒 (防 429),
``temperature`` 0.01、``max_tokens`` 32, 结果清洗后先精确匹配再子串匹配,
并打一条 debug 日志打印接口完整返回.
"""

from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import httpx
from loguru import logger

DEFAULT_VISION_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_VISION_MODEL = "glm-4v-flash"
# 内置默认 Key (原先硬编码在 terminus.py 里的共享 Key, 现保留为默认值):
# 未在面板 / 环境变量里填自己的 Key 时就用它; 共享额度易限流, 建议在面板填自己的 Key.
DEFAULT_VISION_API_KEY = "2eade9f1396646bfb64a0fe3ab8afba3.6H8UfxWTsqeGMsFQ"

_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"
_VISION_TIMEOUT = 30
_VISION_MAX_ATTEMPTS = 3
_VISION_RETRY_DELAY = 3


def normalize_vision_base_url(url: Optional[str]) -> str:
    """归一化 base_url: 去空白、去尾斜杠、去掉已带的 /chat/completions."""
    value = (url or "").strip()
    if not value:
        return DEFAULT_VISION_BASE_URL
    value = value.rstrip("/")
    if value.endswith(_CHAT_COMPLETIONS_SUFFIX):
        value = value[: -len(_CHAT_COMPLETIONS_SUFFIX)].rstrip("/")
    return value or DEFAULT_VISION_BASE_URL


def chat_completions_url(base_url: Optional[str]) -> str:
    """由 base_url 拼出实际要 POST 的完整端点."""
    return normalize_vision_base_url(base_url) + _CHAT_COMPLETIONS_SUFFIX


@dataclass(frozen=True)
class VisionSettings:
    """解析后的视觉配置 (调用方只读这一份, 不再各自拼环境变量)."""

    base_url: str
    api_key: str
    model: str
    # 实际生效的环境变量名 (面板修改被环境变量覆盖时, 用于在面板里提示).
    env_overrides: Tuple[str, ...] = ()


def _config_value(config_vision: Any, name: str) -> str:
    if config_vision is None:
        return ""
    if isinstance(config_vision, dict):
        value = config_vision.get(name)
    else:
        value = getattr(config_vision, name, None)
    return (value or "").strip() if isinstance(value, str) else ""


def resolve_vision_config(config_vision: Any = None) -> VisionSettings:
    """按 环境变量 > 配置文件 > 默认值 的顺序解析视觉配置."""
    env_base = os.environ.get("VISION_BASE_URL", "").strip()
    env_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    env_model = os.environ.get("ZHIPU_VISION_MODEL", "").strip()

    overrides = tuple(
        name
        for name, value in (
            ("VISION_BASE_URL", env_base),
            ("ZHIPU_API_KEY", env_key),
            ("ZHIPU_VISION_MODEL", env_model),
        )
        if value
    )

    base_url = normalize_vision_base_url(
        env_base or _config_value(config_vision, "base_url") or DEFAULT_VISION_BASE_URL
    )
    api_key = env_key or _config_value(config_vision, "api_key") or DEFAULT_VISION_API_KEY
    model = env_model or _config_value(config_vision, "model") or DEFAULT_VISION_MODEL
    return VisionSettings(
        base_url=base_url, api_key=api_key, model=model, env_overrides=overrides
    )


def build_prompt(options: List[str]) -> str:
    """构造让模型从候选项中只输出一项的 prompt."""
    return (
        "请根据图片内容，从候选项中选出最匹配的一项。\n"
        "候选项如下：\n" + "\n".join("- " + x for x in options) + "\n\n"
        "要求：\n"
        "1. 只能输出候选项中的一个；\n"
        "2. 不要解释；\n"
        "3. 不要输出多余文字；\n"
        "4. 如果不确定，也必须从候选项中选最可能的一项。"
    )


def clean_result(text: str) -> str:
    """清理模型输出中的引号、标点与空白, 便于与候选文本比对."""
    result = (text or "").strip()
    for ch in ("“", "”", '"', "'", "。", "，", ",", "：", ":", " ", "\n", "\t"):
        result = result.replace(ch, "")
    return result.strip()


def match_option(content: str, options: List[str], log=None) -> Optional[str]:
    """把模型返回的文本映射回某个候选项, 失败返回 None."""
    result = clean_result(content or "")
    (log or logger).debug("智谱提取出的结果: %s", result)
    if result in options:
        return result
    # 模型偶尔会带前缀 (如 "答案: A"), 退化为子串匹配
    for option in options:
        if option and option in result:
            return option
    (log or logger).warning("智谱返回了无效结果: %s", result)
    return None


async def recognize_image(
    image_bytes: bytes,
    options: List[str],
    settings: VisionSettings,
    log=None,
) -> Optional[str]:
    """调用视觉模型识别图片, 从候选项中返回最匹配的一项.

    逻辑照抄用户版 terminus.py 的 ``_zhipu_visual`` + 外层重试:
    最多尝试 3 次, 每次失败后等 3 秒再试 (防 429); 全部失败返回 None.
    ``log`` 为调用方的 logger (如签到器的 ``self.log``), 不传则用模块 logger.
    """
    log = log or logger
    if not settings.api_key:
        log.warning("未设置 ZHIPU_API_KEY 环境变量.")
        return None
    if not image_bytes:
        log.warning("验证码图片为空, 无法识别.")
        return None

    image_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": settings.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": build_prompt(options)},
                ],
            }
        ],
        # 将 0 改为 0.01, 避免智谱 API 因极端值报错或返回空内容
        "temperature": 0.01,
        "max_tokens": 32,
    }
    headers = {
        "Authorization": "Bearer " + settings.api_key,
        "Content-Type": "application/json",
    }
    url = chat_completions_url(settings.base_url)

    for attempt in range(_VISION_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=_VISION_TIMEOUT) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                # 打印接口完整的返回内容, 如果再出现"无效结果", 可以通过这条日志查明原因
                log.debug(f"智谱接口完整返回内容: {data}")
                content = data["choices"][0]["message"]["content"]
                result = match_option(content, options, log)
                if result:
                    return result
        except Exception as e:
            log.warning("智谱视觉识别失败: %s", e)
        if attempt < _VISION_MAX_ATTEMPTS - 1:
            log.warning("智谱解析失败, 正在重试解析 (%s/3).", attempt + 1)
            log.info("等待 3 秒后进行下一次重试...")
            await asyncio.sleep(_VISION_RETRY_DELAY)
    return None
