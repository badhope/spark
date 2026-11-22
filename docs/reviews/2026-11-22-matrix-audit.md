# 竞品矩阵逐行核对（DOC-01，2026-11-22）

## 范围

- 对象：`docs/competitive-comparison.md` 的 spark 列。
- 数量：86 个 ✅ 行（不含竞品列的 ✅）。
- 目标：每个 ✅ 行都有代码或测试落点；发现“文档说已有、代码没有”的假话并修正。
- 边界：只核对本仓库实现，不安装/实测 Qoder、ZCode、opencode、Codex CLI；竞品列仍按其公开文档与仓库声明。

## 方法

1. 用脚本逐行解析表格，筛出 spark 列为 ✅ 但整行没有任何反引号代码标识、`tests/` 或 `docs/` 落点的行。
2. 对筛出的 36 行逐项 `rg` 源码与测试，确认真实入口、函数或测试文件后，把落点内联回矩阵。
3. 对疑似夸大的行做反向核对：搜端点、函数名、配置项与测试断言；找不到实现就降级，不保留 ✅。

复核命令示例：

```bash
rg -n 'checkpoint|baseline' src/spark/core/checkpoints.py
rg -n 'call_signature|allow_always' src/spark/policy.py src/spark/core/tool_runner.py
rg -n 'html|share|export|rename' src/spark/web/_handlers_sessions.py
rg -n 'archive' src/spark/web/_handlers_sessions.py src/spark/web/routes.py web/src/components/Sidebar.tsx
```

## 修正

| 行 | 原判断 | 核对结果 | 处理 |
| --- | --- | --- | --- |
| 归档 | ✅ Web 删除 | 后端与前端只有删除会话，没有归档状态、归档列表或恢复入口 | 降为 ⚠️“Web 仅删除会话，无归档/恢复” |
| 重命名 | ✅ 自动标题 | 自动标题存在，Web 侧栏也有手动改名（`/api/sessions/rename`） | 保留 ✅，补手动改名落点 |
| 审批弹窗粒度 | ✅ 相同签名复用 | `policy.py:call_signature` + `core/tool_runner.py` 的 `allow_always` 集合确实按签名复用，且受仓库层 deny 压制 | 保留 ✅，补精确落点 |
| 分享链接 | ✅ 单文件 HTML | `_handle_session_export` 的 `html` 分支生成自包含文件，内容经 `html.escape` | 保留 ✅，补端点与前端入口 |
| 快照/回滚 | ✅ tar.gz + baseline | `core/checkpoints.py` 明确实现 tar.gz、manifest 与 baseline 增量 | 保留 ✅，补测试 |

## 结果

- 36 个原本没有代码/测试落点的 ✅ 行已补齐，复查脚本对“spark 列 ✅ 且无落点”的输出为 0。
- 1 个假 ✅（归档）降为 ⚠️；矩阵顶部复测日期更新为 2026-11-22（第八轮）。
- 剩余 ✅ 行原有落点保留；本轮没有新增未实现的能力声明。

## 复跑

```bash
python - <<'PY'
from pathlib import Path
for i, line in enumerate(Path('docs/competitive-comparison.md').read_text().splitlines(), 1):
    if line.startswith('|'):
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) > 1 and '✅' in cells[1]:
            spark = cells[1]
            if '`' not in spark and 'test' not in spark.lower():
                print(i, cells[0])
PY
```

预期：无输出。

## 未覆盖

- 竞品侧没有本地安装实测，不能把其文档声明当作运行验证。
- 本核对证明“有代码/测试落点”，不证明每条链路都在本轮重新跑过；涉及 UI 的行由 `tests/test_ui_*` 与浏览器回归覆盖，涉及后端的行为由对应单测覆盖。
