"""终点站视觉识别的测试 (不依赖 Telegram / 模型服务).

覆盖:
- 纯函数 embykeeper.vision: base_url 归一化、默认值、
  环境变量 > 配置文件 > 默认值 的优先级.
- 结果清洗与匹配 (clean_result / match_option).
- 异步识别 recognize_image (httpx 调用全部 mock 掉): 成功、子串匹配、
  重试逻辑 (失败重试 3 次, 每次等 3 秒, 这里把 sleep mock 掉以便快速跑).
schema 层面的默认值测试在 pydantic 可用时才跑 (本地裸环境没有依赖时会跳过,
CI 里会跑).
"""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from embykeeper.vision import (
    DEFAULT_VISION_API_KEY,
    DEFAULT_VISION_BASE_URL,
    DEFAULT_VISION_MODEL,
    VisionSettings,
    chat_completions_url,
    clean_result,
    match_option,
    normalize_vision_base_url,
    recognize_image,
    resolve_vision_config,
)

try:
    from embykeeper.schema import Config as CoreConfig
    from embykeeper.schema import VisionConfig
except Exception:  # pragma: no cover - 本地无依赖时跳过 schema 测试
    CoreConfig = None
    VisionConfig = None

_ENV_KEYS = ("VISION_BASE_URL", "ZHIPU_API_KEY", "ZHIPU_VISION_MODEL")


class VisionNormalizeTest(unittest.TestCase):
    def test_full_endpoint_is_stripped(self):
        self.assertEqual(
            normalize_vision_base_url("https://open.bigmodel.cn/api/paas/v4/chat/completions"),
            "https://open.bigmodel.cn/api/paas/v4",
        )

    def test_trailing_slash_is_stripped(self):
        self.assertEqual(
            normalize_vision_base_url("https://example.com/v1/"),
            "https://example.com/v1",
        )

    def test_empty_falls_back_to_default(self):
        self.assertEqual(normalize_vision_base_url(""), DEFAULT_VISION_BASE_URL)
        self.assertEqual(normalize_vision_base_url(None), DEFAULT_VISION_BASE_URL)

    def test_chat_completions_url_does_not_double_suffix(self):
        self.assertEqual(
            chat_completions_url("https://open.bigmodel.cn/api/paas/v4/chat/completions"),
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        )
        self.assertEqual(
            chat_completions_url("https://example.com/v1"),
            "https://example.com/v1/chat/completions",
        )


class VisionResolveTest(unittest.TestCase):
    def setUp(self):
        self._saved = {key: os.environ.get(key) for key in _ENV_KEYS}
        for key in _ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults_when_nothing_configured(self):
        settings = resolve_vision_config()
        self.assertEqual(settings.base_url, DEFAULT_VISION_BASE_URL)
        self.assertEqual(settings.model, DEFAULT_VISION_MODEL)
        # 默认值即原先硬编码的共享 Key: 没填自己的 Key 时仍能直接用.
        self.assertEqual(settings.api_key, DEFAULT_VISION_API_KEY)
        self.assertTrue(settings.api_key)
        self.assertEqual(settings.env_overrides, ())

    def test_config_overrides_defaults(self):
        settings = resolve_vision_config(
            {"base_url": "https://example.com/v1", "api_key": "panel-key", "model": "panel-model"}
        )
        self.assertEqual(settings.base_url, "https://example.com/v1")
        self.assertEqual(settings.api_key, "panel-key")
        self.assertEqual(settings.model, "panel-model")

    def test_env_overrides_config(self):
        os.environ["VISION_BASE_URL"] = "https://env.example.com/v1/chat/completions"
        os.environ["ZHIPU_API_KEY"] = "env-key"
        os.environ["ZHIPU_VISION_MODEL"] = "env-model"
        settings = resolve_vision_config(
            {"base_url": "https://example.com/v1", "api_key": "panel-key", "model": "panel-model"}
        )
        self.assertEqual(settings.base_url, "https://env.example.com/v1")
        self.assertEqual(settings.api_key, "env-key")
        self.assertEqual(settings.model, "env-model")
        self.assertEqual(
            settings.env_overrides,
            ("VISION_BASE_URL", "ZHIPU_API_KEY", "ZHIPU_VISION_MODEL"),
        )

    def test_empty_env_falls_back_to_config(self):
        os.environ["ZHIPU_API_KEY"] = ""
        settings = resolve_vision_config({"api_key": "panel-key"})
        self.assertEqual(settings.api_key, "panel-key")
        self.assertEqual(settings.env_overrides, ())


@unittest.skipIf(CoreConfig is None, "pydantic 未安装, 跳过 schema 默认值测试")
class VisionSchemaTest(unittest.TestCase):
    def test_config_without_vision_uses_defaults(self):
        model = CoreConfig()
        self.assertEqual(model.vision.base_url, DEFAULT_VISION_BASE_URL)
        self.assertEqual(model.vision.model, DEFAULT_VISION_MODEL)
        self.assertEqual(model.vision.api_key, DEFAULT_VISION_API_KEY)

    def test_config_with_vision_section(self):
        model = CoreConfig(
            vision=VisionConfig(base_url="https://example.com/v1", model="custom-model")
        )
        self.assertEqual(model.vision.base_url, "https://example.com/v1")
        self.assertEqual(model.vision.model, "custom-model")


class VisionCleanMatchTest(unittest.TestCase):
    def test_clean_result_strips_quotes_and_punctuation(self):
        self.assertEqual(clean_result('“A”，'), "A")
        self.assertEqual(clean_result('"B": '), "B")
        self.assertEqual(clean_result(" C\n"), "C")

    def test_match_option_exact(self):
        self.assertEqual(match_option("A", ["A", "B"]), "A")

    def test_match_option_substring(self):
        # 模型偶尔带前缀 (如 "答案: A"), 退化为子串匹配
        self.assertEqual(match_option("答案是A", ["A", "B"]), "A")

    def test_match_option_invalid_returns_none(self):
        self.assertIsNone(match_option("Z", ["A", "B"]))


def _make_settings(**overrides):
    settings = VisionSettings(
        base_url=overrides.get("base_url", DEFAULT_VISION_BASE_URL),
        api_key=overrides.get("api_key", "test-key"),
        model=overrides.get("model", DEFAULT_VISION_MODEL),
    )
    return settings


def _mock_response(content):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _mock_client(post_side_effect):
    """返回一个 mock 的 httpx.AsyncClient (异步上下文管理器)."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=post_side_effect)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class VisionRecognizeTest(unittest.IsolatedAsyncioTestCase):
    async def test_recognize_success_exact_match(self):
        client = _mock_client([_mock_response("A")])
        with patch("embykeeper.vision.httpx.AsyncClient", return_value=client):
            result = await recognize_image(b"fake-image-bytes", ["A", "B"], _make_settings())
        self.assertEqual(result, "A")
        client.post.assert_awaited_once()
        # 请求打到 chat/completions, 带 Bearer 与模型参数
        _, kwargs = client.post.call_args
        self.assertTrue(str(client.post.call_args[0][0]).endswith("/chat/completions"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(kwargs["json"]["model"], DEFAULT_VISION_MODEL)
        self.assertEqual(kwargs["json"]["temperature"], 0.01)
        self.assertEqual(kwargs["json"]["max_tokens"], 32)

    async def test_recognize_substring_match(self):
        client = _mock_client([_mock_response("答案是B")])
        with patch("embykeeper.vision.httpx.AsyncClient", return_value=client):
            result = await recognize_image(b"fake-image-bytes", ["A", "B"], _make_settings())
        self.assertEqual(result, "B")

    async def test_recognize_retries_on_failure_then_succeeds(self):
        client = _mock_client(
            [Exception("boom"), _mock_response("A")]
        )
        with (
            patch("embykeeper.vision.httpx.AsyncClient", return_value=client),
            patch("embykeeper.vision.asyncio.sleep", new=AsyncMock()) as sleep_mock,
        ):
            result = await recognize_image(b"fake-image-bytes", ["A", "B"], _make_settings())
        self.assertEqual(result, "A")
        self.assertEqual(client.post.await_count, 2)
        # 失败后等 3 秒再重试
        sleep_mock.assert_awaited_once_with(3)

    async def test_recognize_retries_on_invalid_result_then_gives_up(self):
        client = _mock_client([_mock_response("Z")] * 3)
        with (
            patch("embykeeper.vision.httpx.AsyncClient", return_value=client),
            patch("embykeeper.vision.asyncio.sleep", new=AsyncMock()) as sleep_mock,
        ):
            result = await recognize_image(b"fake-image-bytes", ["A", "B"], _make_settings())
        self.assertIsNone(result)
        self.assertEqual(client.post.await_count, 3)
        # 3 次尝试, 前 2 次失败后各等 3 秒, 第 3 次后不再等
        self.assertEqual(sleep_mock.await_count, 2)
        sleep_mock.assert_awaited_with(3)

    async def test_recognize_no_api_key_returns_none_without_calling(self):
        client = _mock_client([_mock_response("A")])
        settings = _make_settings(api_key="")
        with patch("embykeeper.vision.httpx.AsyncClient", return_value=client):
            result = await recognize_image(b"fake-image-bytes", ["A", "B"], settings)
        self.assertIsNone(result)
        client.post.assert_not_awaited()

    async def test_recognize_empty_image_returns_none_without_calling(self):
        client = _mock_client([_mock_response("A")])
        with patch("embykeeper.vision.httpx.AsyncClient", return_value=client):
            result = await recognize_image(b"", ["A", "B"], _make_settings())
        self.assertIsNone(result)
        client.post.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
