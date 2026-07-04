"""
config 的**离线**单测（绝不联网、不发任何真实 LLM 调用）。

策略：用 patch.dict 临时移除 DEEPSEEK_API_KEY，断言 require_api_key()
在启动前能给出清晰配置错误，而不是等节点运行时才失败。

运行：
    langgraph_cs/.venv/bin/python -m langgraph_cs.nodes.tests.test_config_offline
"""
import os
from unittest.mock import patch

from langgraph_cs import config as config_mod


def test_require_api_key_fails_without_env():
    """缺 DEEPSEEK_API_KEY 时抛 RuntimeError，错误信息指向 .env 配置。"""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            config_mod.require_api_key()
        except RuntimeError as ex:
            message = str(ex)
        else:
            raise AssertionError("require_api_key() should fail without DEEPSEEK_API_KEY")

    assert ".env" in message, message
    print("✓ 缺 DEEPSEEK_API_KEY：require_api_key() 抛 RuntimeError，错误信息包含 .env")


def test_build_session_config_omits_empty_session_user_id():
    """空身份必须省略 session_user_id；非空身份 trim 后注入。"""
    assert config_mod.build_session_config("t-1") == {"configurable": {"thread_id": "t-1"}}
    assert config_mod.build_session_config("t-2", "   ") == {"configurable": {"thread_id": "t-2"}}
    assert config_mod.build_session_config("t-3", " user_001 ") == {
        "configurable": {"thread_id": "t-3", "session_user_id": "user_001"}
    }
    print("✓ build_session_config：空身份省略，非空身份 trim 后注入")


def _run_all():
    tests = [
        test_require_api_key_fails_without_env,
        test_build_session_config_omits_empty_session_user_id,
    ]
    for t in tests:
        t()
    print("\n全部 config 离线用例通过 ✅（API key 启动前校验，不联网）")


if __name__ == "__main__":
    _run_all()
