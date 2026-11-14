"""内置合成 fixtures（P1-6）。

两类风格：
- ``swe-lite-style``：SWE-bench Lite 范式的 toy 复现——给一个带 bug 的小仓库 +
  pytest 测试文件，agent 修好代码后由 harness 跑 pytest 判定（对齐 SWE-bench 的
  FAIL_TO_PASS 验证方式，只是仓库换成自包含 toy 以保证 CI 离线可跑）。
- ``tau-bench-style``：τ-bench 范式的 agentic 工具轨迹判定——客服任务必须按
  正确顺序调用正确工具（查订单 → 登记退款 / 查物流 → 回单号）。
"""

from __future__ import annotations

from spark.evals.harness import Scenario

CALC_BUGGY = '"""toy math module (bug injected for eval)."""\n\n\ndef add(a, b):\n    # BUG: 写成了减法\n    return a - b\n\n\ndef scale(value, factor=1.0):\n    return value * factor\n'

CALC_FIXED = '"""toy math module (fixed)."""\n\n\ndef add(a, b):\n    return a + b\n\n\ndef scale(value, factor=1.0):\n    return value * factor\n'

CALC_TEST = """from calc import add, scale


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_scale():
    assert scale(4, 2) == 8
"""

PARSER_BUGGY = """def first_last(items):
    # BUG: 首项取成了 items[1]
    return items[1], items[-1]
"""

PARSER_FIXED = """def first_last(items):
    return items[0], items[-1]
"""

PARSER_TEST = """from parser import first_last


def test_first_last():
    assert first_last(["a", "b", "c"]) == ("a", "c")
"""

ORDERS_JSON = '{"ORD-789": {"amount": 120.0, "status": "paid", "item": "wireless mouse"}}'

REFUND_LOG = '{"order": "ORD-789", "amount": 120.0, "action": "refunded", "note": "customer requested full refund"}'

SHIPMENT_TXT = "tracking: SF-998877\nstatus: in_transit\neta: 2026-10-20\n"


def builtin_scenarios() -> list[Scenario]:
    return [
        Scenario(
            id="swe-toy-calc-add",
            suite="swe-lite-style",
            prompt=(
                "仓库里 calc.py 的 add() 把加法实现成了减法（FAIL_TO_PASS: test_add）。"
                "请修复 calc.py，并运行 pytest 确认测试通过。"
            ),
            setup_files={
                "calc.py": CALC_BUGGY,
                "test_calc.py": CALC_TEST,
            },
            script=[
                [
                    {
                        "type": "tool_call",
                        "tool_call": {
                            "id": "c1",
                            "name": "write_file",
                            "arguments": {"path": "calc.py", "content": CALC_FIXED},
                        },
                    }
                ],
                [
                    {
                        "type": "tool_call",
                        "tool_call": {
                            "id": "c2",
                            "name": "run_shell",
                            "arguments": {"command": "{python} -m pytest -q test_calc.py"},
                        },
                    }
                ],
                [
                    {
                        "type": "text",
                        "text": "已修复 add() 为 return a + b，pytest 全部通过（2 passed）。",
                    }
                ],
            ],
            judges=[
                {"type": "file_contains", "path": "calc.py", "contains": ["return a + b"]},
                {
                    "type": "run_command",
                    "command": "{python} -m pytest -q test_calc.py",
                    "expect_returncode": 0,
                    "timeout_sec": 120,
                },
            ],
            metadata={"style": "SWE-bench FAIL_TO_PASS on toy repo"},
        ),
        Scenario(
            id="swe-toy-parser-first",
            suite="swe-lite-style",
            prompt=(
                "parser.py 的 first_last() 取首项时下标错了（应该是 items[0]）。请修复并跑测试。"
            ),
            setup_files={
                "parser.py": PARSER_BUGGY,
                "test_parser.py": PARSER_TEST,
            },
            script=[
                [
                    {
                        "type": "tool_call",
                        "tool_call": {
                            "id": "c1",
                            "name": "write_file",
                            "arguments": {"path": "parser.py", "content": PARSER_FIXED},
                        },
                    }
                ],
                [
                    {
                        "type": "text",
                        "text": "已修复 first_last()，test_first_last 通过。",
                    }
                ],
            ],
            judges=[
                {"type": "file_contains", "path": "parser.py", "contains": ["items[0]"]},
                {
                    "type": "run_command",
                    "command": "{python} -m pytest -q test_parser.py",
                    "expect_returncode": 0,
                    "timeout_sec": 120,
                },
            ],
            metadata={"style": "SWE-bench FAIL_TO_PASS on toy repo"},
        ),
        Scenario(
            id="tau-toy-order-refund",
            suite="tau-bench-style",
            prompt=(
                "客户来电：订单 ORD-789 要求全额退款。请先读取订单文件确认金额，"
                "再登记退款日志 refund_log.json，最后回复客户退款结果。"
            ),
            setup_files={"orders.json": ORDERS_JSON},
            script=[
                [
                    {
                        "type": "tool_call",
                        "tool_call": {
                            "id": "c1",
                            "name": "read_file",
                            "arguments": {"path": "orders.json"},
                        },
                    }
                ],
                [
                    {
                        "type": "tool_call",
                        "tool_call": {
                            "id": "c2",
                            "name": "write_file",
                            "arguments": {"path": "refund_log.json", "content": REFUND_LOG},
                        },
                    }
                ],
                [
                    {
                        "type": "text",
                        "text": "已为订单 ORD-789 完成全额退款 120.0 元，退款日志已登记。",
                    }
                ],
            ],
            judges=[
                {
                    "type": "tool_sequence",
                    "calls": [
                        {"name": "read_file", "args_contains": {"path": "orders.json"}},
                        {"name": "write_file", "args_contains": {"path": "refund_log.json"}},
                    ],
                },
                {
                    "type": "file_contains",
                    "path": "refund_log.json",
                    "contains": ["ORD-789", "refunded"],
                },
                {"type": "final_text_contains", "contains": ["ORD-789", "退款"]},
            ],
            metadata={"style": "τ-bench tool-trajectory compliance"},
        ),
        Scenario(
            id="tau-toy-shipment-lookup",
            suite="tau-bench-style",
            prompt=(
                "客户问订单 ORD-555 现在到哪了。请在 shipments/ 下找到对应文件，"
                "读出物流单号并回复客户。"
            ),
            setup_files={"shipments/ORD-555.txt": SHIPMENT_TXT},
            script=[
                [
                    {
                        "type": "tool_call",
                        "tool_call": {
                            "id": "c1",
                            "name": "glob",
                            "arguments": {"pattern": "shipments/*"},
                        },
                    }
                ],
                [
                    {
                        "type": "tool_call",
                        "tool_call": {
                            "id": "c2",
                            "name": "read_file",
                            "arguments": {"path": "shipments/ORD-555.txt"},
                        },
                    }
                ],
                [
                    {
                        "type": "text",
                        "text": "您的订单 ORD-555 物流单号是 SF-998877，目前在途，预计 2026-10-20 送达。",
                    }
                ],
            ],
            judges=[
                {
                    "type": "tool_sequence",
                    # Listing first is one valid path; reading the known file
                    # directly is equally valid and is what real models do.
                    "calls": [
                        {
                            "name": "glob",
                            "alternatives": ["list_dir", "read_file"],
                            "args_optional": True,
                        },
                        {"name": "read_file", "args_contains": {"path": "ORD-555"}},
                    ],
                },
                {"type": "final_text_contains", "contains": ["SF-998877"]},
            ],
            metadata={"style": "τ-bench tool-trajectory compliance"},
        ),
    ]
