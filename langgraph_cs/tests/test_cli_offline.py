"""
CLI 审批交互的**离线**单测（不联网、不建图、不读真实 stdin）。

覆盖本轮改动里最容易写错的两个纯函数：
  - _parse_approval_input：y/n 判定、全角标点/全角空格容错、无法识别返回 None；
  - _interrupt_payload：kind 缺省 seat 兼容、显式 None 字段回落默认值、params 非 dict 防御。
外加 _read_approval_resume 的"未识别 -> 重新询问"循环（mock input 序列验证）。

运行：
    langgraph_cs/.venv/bin/python -m langgraph_cs.tests.test_cli_offline
"""
import builtins

from langgraph_cs import main as main_mod


class _FakeInterrupt:
    def __init__(self, value):
        self.value = value


def test_parse_approval_input():
    """决策词 + 备注切分：半角/全角输入都要正确；识别不了返回 None 而不是驳回。"""
    cases = [
        ("y 已电话核实", {"approved": True, "note": "已电话核实"}),
        ("y，请尽快处理", {"approved": True, "note": "请尽快处理"}),   # 全角逗号粘连
        ("同意！", {"approved": True, "note": ""}),                    # 全角叹号
        ("n　资料不全", {"approved": False, "note": "资料不全"}),      # 全角空格
        ("驳回：超出时限", {"approved": False, "note": "超出时限"}),   # 全角冒号
        ("拒绝", {"approved": False, "note": ""}),
        ("ok", None),           # 不在词表：交回调用方重新询问
        ("n资料不全", None),    # 决策词与备注粘连无分隔：宁可再问，不猜
        ("", None),
    ]
    for raw, expected in cases:
        got = main_mod._parse_approval_input(raw)
        assert got == expected, (raw, got, expected)
    print("✓ _parse_approval_input：全角标点/空格容错，未识别返回 None（绝不静默驳回）")


def test_read_approval_resume_reasks_until_recognized():
    """未识别的输入触发重新询问；识别后返回结构化 resume，批准意图不会被反转。"""
    answers = iter(["ok", "随便说的", "y，已核实无误"])
    orig_input = builtins.input
    builtins.input = lambda _prompt="": next(answers)
    try:
        out = main_mod._read_approval_resume({"prompt": "待人工审批", "params": {"order_id": "ORD-1"}})
    finally:
        builtins.input = orig_input
    assert out == {"approved": True, "note": "已核实无误"}, out
    print("✓ _read_approval_resume：连续两次未识别 -> 重新询问 -> 第三次批准生效")


def test_read_approval_resume_eof_rejects():
    """EOF/Ctrl-C：按驳回处理（fail-safe），不批准、不崩。"""
    def _raise(_prompt=""):
        raise EOFError

    orig_input = builtins.input
    builtins.input = _raise
    try:
        out = main_mod._read_approval_resume({"prompt": "待人工审批", "params": {}})
    finally:
        builtins.input = orig_input
    assert out == {"approved": False, "note": "坐席未审批"}, out
    print("✓ _read_approval_resume：EOF -> 驳回兜底")


def test_interrupt_payload_defaults():
    """kind 缺省 seat；显式 None 字段回落默认；params 非 dict 归 {}。"""
    # 早期格式的转人工 payload（无 kind），验证兼容分支
    legacy = {"__interrupt__": [_FakeInterrupt({"prompt": "请回复", "user_message": "你好"})]}
    out = main_mod._interrupt_payload(legacy)
    assert out["kind"] == "seat" and out["prompt"] == "请回复", out

    # 畸形 payload：显式 None + params 非 dict
    weird = {"__interrupt__": [_FakeInterrupt({"kind": None, "prompt": None, "params": "oops"})]}
    out = main_mod._interrupt_payload(weird)
    assert out["kind"] == "seat", out
    assert out["prompt"] == "需要人工处理，请输入回复：", out
    assert out["params"] == {}, out

    # 非 dict value：整体回落 seat 默认
    out = main_mod._interrupt_payload({"__interrupt__": [_FakeInterrupt("just text")]})
    assert out["kind"] == "seat", out
    print("✓ _interrupt_payload：kind 缺省 / 显式 None / params 非 dict 均正确兜底")


def _run_all():
    tests = [
        test_parse_approval_input,
        test_read_approval_resume_reasks_until_recognized,
        test_read_approval_resume_eof_rejects,
        test_interrupt_payload_defaults,
    ]
    for t in tests:
        t()
    print("\n全部 CLI 离线用例通过 ✅（审批输入解析 + interrupt payload 兜底，不联网）")


if __name__ == "__main__":
    _run_all()
