"""Tests for the panel manual-action helpers (feature set 2026-11-05).

- ``_validate_raw_config``: raw config.toml text -> parsed config dict.
- ``_validate_manual_checkin_sites``: split a manual check-in request into
  valid / unknown sites, de-duplicated, order-preserving.

Both helpers live in FastAPI modules, so the whole module is skipped when
the backend dependencies are not installed (same pattern as test_vision.py).
"""

import unittest

try:
    from embykeeper.api.config import _validate_raw_config
    from embykeeper.api.telegram import _validate_manual_checkin_sites

    _BACKEND_AVAILABLE = True
except Exception:  # pragma: no cover - missing backend deps
    _BACKEND_AVAILABLE = False


@unittest.skipUnless(_BACKEND_AVAILABLE, "backend dependencies not installed")
class TestValidateRawConfig(unittest.TestCase):
    def test_empty_text_is_valid(self):
        self.assertEqual(_validate_raw_config(""), {})
        self.assertEqual(_validate_raw_config("  \n # only a comment\n"), {})

    def test_valid_toml_returns_parsed_config(self):
        parsed = _validate_raw_config(
            '[checkiner]\nenabled = true\ntimeout = 120\n'
        )
        self.assertEqual(parsed["checkiner"]["enabled"], True)
        self.assertEqual(parsed["checkiner"]["timeout"], 120)

    def test_malformed_toml_raises(self):
        with self.assertRaises(ValueError):
            _validate_raw_config("[checkiner\nbad = = =\n")

    def test_wrong_value_type_raises(self):
        with self.assertRaises(ValueError):
            _validate_raw_config('[vision]\nbase_url = 123\n')

    def test_wrong_section_type_raises(self):
        with self.assertRaises(ValueError):
            _validate_raw_config('telegram = "not-a-table"\n')


@unittest.skipUnless(_BACKEND_AVAILABLE, "backend dependencies not installed")
class TestValidateManualCheckinSites(unittest.TestCase):
    def test_splits_valid_and_unknown(self):
        valid, unknown = _validate_manual_checkin_sites(
            ["terminus", "charon", "nope"], {"terminus", "charon"}
        )
        self.assertEqual(valid, ["terminus", "charon"])
        self.assertEqual(unknown, ["nope"])

    def test_dedupes_and_strips(self):
        valid, unknown = _validate_manual_checkin_sites(
            [" terminus ", "terminus", ""], {"terminus"}
        )
        self.assertEqual(valid, ["terminus"])
        self.assertEqual(unknown, [])

    def test_empty_request(self):
        self.assertEqual(_validate_manual_checkin_sites([], {"terminus"}), ([], []))
        self.assertEqual(_validate_manual_checkin_sites(None, {"terminus"}), ([], []))


@unittest.skipUnless(_BACKEND_AVAILABLE, "backend dependencies not installed")
class TestManualCheckinEndpoint(unittest.TestCase):
    def test_unknown_phone_returns_404_not_500(self):
        """回归: 手动签到接口曾把 runinfo.py 的调用方式抄过来,
        写成 _active_config_file(config_manager), 而 telegram.py 的同名
        函数不接受参数, 导致任何手动签到都 500。未知账号应走到
        账号查找并返回 404, 且 reload_conf 只收到一个路径参数。"""
        from unittest.mock import AsyncMock, patch

        from fastapi.testclient import TestClient

        import embykeeper.api.telegram as telegram_api
        from embykeeper.api import create_app
        from embykeeper.api.auth import require_auth
        from embykeeper.config import ConfigManager
        from embykeeper.schema import Config

        app = create_app()
        app.dependency_overrides[require_auth] = lambda: True
        # config_manager 是 ConfigManager 代理对象, 其 reload_conf 定义在类上,
        # 需按类打补丁 (直接 patch 实例属性会被代理的 __getattribute__ 拦住)。
        with (
            patch.object(
                ConfigManager, "reload_conf", new=AsyncMock()
            ) as reload_mock,
            patch.object(telegram_api, "_load_config", return_value=Config()),
        ):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/api/telegram/accounts/+8613800000000/checkin",
                    json={"sites": ["terminus"]},
                )
        self.assertEqual(resp.status_code, 404)
        reload_mock.assert_awaited_once()
        # reload_conf() 不带参数: 由当前配置源 (文件或 MongoDB) 决定重载位置。
        self.assertEqual(len(reload_mock.await_args.args), 0)


if __name__ == "__main__":
    unittest.main()
