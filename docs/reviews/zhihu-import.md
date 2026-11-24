# AI 编程工具都在卷生成，为什么我更在意“测试失败之后”

现在打开任何一个 AI 编程产品的首页，看到的都是“一句话生成一个应用”。我不怀疑生成能力，我怀疑的是交付能力。

写代码是一个动作，交付是一条链：读仓库、改代码、跑测试、看失败、再改、复核 diff、留下可恢复的状态。生成只占其中一小段。

### 一、先让测试说话

很多 AI 编程产品的演示停在“生成了一段代码”。这段代码能不能跑、有没有破坏别的文件、测试失败之后谁来收拾，演示里通常不讲。

Spark 的 Autopilot 只做一件事：把需求丢进去，让 Agent 自己读仓库、改代码、跑测试、看失败、再改，直到测试通过，再做一轮交付复核。

状态机只有四个阶段：

```text
build → repair → audit → done
```

关键不在名字，在约束：

- 测试由运行器独立执行，不采信模型说“我完成了”；
- 失败输出、traceback 原样回灌给下一轮，不总结、不美化；
- 测试通过也不直接交付，必须进入 `audit`，再独立复验一次；
- 测试通过但 diff 为空，直接判 `failed`；
- 每轮状态写到 `.spark/autopilot/<run-id>.json`，支持 `--resume`；
- 轮次、单轮超时、总时限、成本、连续模型错误、连续无进展，任何一项都能熔断；
- 默认 `workspace` 沙箱、默认无网络；要 `unrestricted` 必须显式确认。

这套约束听起来很“工程”，不像 Demo 那么好看。但 AI 编程真正缺的就是这个：不是再生成一段代码，而是让变更可验证、可回滚、可审计。

### 二、四轮，不是一次

我拿本地 MiniCPM5-2B-Q4_K_M 跑了一个独立小仓库。初始代码：

```python
def add(a: int, b: int) -> int:
    return a - b
```

测试：

```python
def test_add_positive_numbers():
    assert add(2, 3) == 5
```

第一次跑，4 轮、11 次工具调用，build → repair(2) → audit，最后 `passed`。最终代码是 `return a + b`，独立测试 `python -m pytest -q` 通过，audit 轮又跑了一次。

后来在最终代码上重跑了一次干净仓库：4 轮、8 次工具调用，同样 `passed`。

这两次都不漂亮。模型第一轮没改对，第二轮也没改对，第三轮才把 `-` 改成 `+`。但这就是真实模型的样子：它不是一次成稿，它需要在失败信息里慢慢收敛。

同一任务换 1B 模型，8 轮、0 次工具调用，没完成。这不是运行器假通过，也不是“小模型也能行”的营销话术，就是能力边界。

### 三、为什么一定要有 audit

只跑测试有个漏洞：测试可能只覆盖了主路径。模型把测试跑绿了，但顺手改了测试、放宽了断言、或者留下一个无关的大 diff，测试也会绿。

所以测试通过之后，Spark 还要再走一轮 `audit`：复核需求覆盖、检查 diff 边界、再跑一次独立测试。只有“测试通过 + 真实代码变更 + 复核完成”才允许 `passed`。

这轮复核不是让模型自夸，而是给它一个更窄的问题：这次变更有没有超出需求，有没有碰不该碰的文件，测试是不是被改过。

### 四、开源生态不是拿来摆的

Spark 没有把所有东西都自己造。

代码检索升级成了符号感知分块：按顶层 `class/function` 切，方法归属到类，长函数保留 symbol 续窗；文件内容用 SHA256 做增量缓存，只重新嵌入新增或变更的 chunk；检索时 BM25、向量、精确短语、路径和 symbol 精确匹配一起重排。

OpenCode 的静态资产也能直接读：`opencode.json(c)`、`.opencode/agents/**/*.md`、`.opencode/commands/*.md`，权限里的 `deny` 会映射成 Spark 的 `denied_tools`。但插件运行时、provider 注册表、MCP 自动导入这些，我没有假装支持。兼容到什么程度，文档里写清楚。

这部分我写了一份 `docs/OPENCODE_COMPAT.md`，也留了 `spark opencode --workdir .` 的只读检查命令。

### 五、它现在还不能做什么

这篇不是发布胜利宣言。Spark 现在有几件事没做完：

- 没有外部真实仓库、20 回合以上的基准；2B 的 4 轮验收只是最小闭环，不是长任务证明；
- 没有真实 `kill -9` 中途崩溃恢复测试；状态文件能恢复“上一轮完成之后”，不能证明“写到一半被杀”；
- Windows/macOS 的签名、公证还缺证书；
- 全仓仍有历史高复杂度函数，`radon` 口径还有 70 个 D/E/F；这轮只保证新增代码和主链路不继续抬复杂度；
- 五社区的实际发布还没做完，B 站账号需要重新登录。

能确认的是这些数字：

- `ruff check src tests scripts` 通过；
- `mypy src/spark --ignore-missing-imports`：189 个源文件，0 error；
- 全量 pytest：1998 项，1982 passed、16 skipped、0 failed，978.48 秒；
- Autopilot 合成压力矩阵：120/120；
- 真实本地模型：2B 两次 4 轮 passed；1B 8 轮 0 工具调用 failed。

### 六、链接

代码在五个平台同步，主入口：

- GitHub：https://github.com/badhope/spark
- Gitee：https://gitee.com/badhope/spark
- GitCode：https://gitcode.com/badhope/spark

如果你也在做 coding agent，欢迎拿一个真实仓库来砸它。最好是 20 回合以上、跨文件、要装依赖的那种。那才是这类工具真正的考场。
