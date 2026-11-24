# 跨语言符号图与调用链

> 入口：`spark symbols`；工具：`find_symbol` 的 `callers` / `callees` / `impact` 模式。
> 代码：`src/spark/symbol_graph.py`；测试：`tests/test_symbol_graph.py`。

## 1. 它解决什么问题

`repo map` 回答“哪个文件里有哪些符号”，`grep` 回答“这段字符串在哪”，LSP 回答“语义上的定义与引用”。三者之间还缺一个轻量问题：

> 改这个函数，谁会受影响？这个函数又调用了谁？

`symbol_graph` 用仓库已有的 tree-sitter 语法给出**语法级调用图**，覆盖 Python、TypeScript/TSX、JavaScript、Go、Rust。它不启动语言服务器、不执行仓库代码，适合在改动前做有界的影响面估算。

## 2. 命令行

```bash
# 列出符号与调用边统计
spark symbols --mode list --workdir .

# 查看一个符号的定义、调用者、被调用者
spark symbols helper --workdir .
spark symbols helper --mode callers --depth 2 --workdir .
spark symbols helper --mode callees --depth 1 --workdir .
spark symbols helper --mode impact --depth 3 --workdir .

# 机器可读
spark symbols helper --mode impact --json --workdir .
```

输出示例：

```text
# Symbol: helper
definitions:
  a.py:1  function_definition  helper
callers (depth 2): 1
  a.py:4  main -> a.py:1  [same_file]
callees (depth 2): 0
```

## 3. 工具模式

`find_symbol` 新增三种模式，和原有 `definition` / `reference` / `both` 并存：

| 模式 | 返回 |
|---|---|
| `callers` | 谁调用该符号，支持 `depth` 1–4 |
| `callees` | 该符号调用谁，支持 `depth` 1–4 |
| `impact` | 定义 + 调用者 + 被调用者，形成一份影响面报告 |

示例：

```json
{"symbol":"helper","mode":"impact","depth":2}
```

## 4. 解析规则与置信度

调用边按**简单名**解析，不猜测类型或运行时动态分派：

- `same_file`：同文件内唯一同名定义，优先解析；
- `unique`：全仓库唯一同名定义；
- `ambiguous`：多个同名定义，列出候选但不强行选一个；
- `unresolved`：没有找到定义；可能是外部依赖、动态调用、拼写差异或语法未覆盖。

`unresolved` 不等于“没有调用者”。做删除、改签名、跨模块重构时，仍应结合 `find_symbol mode=reference`、LSP 和测试。

## 5. 边界

- 只读、无执行：不启动语言服务器、不 import 仓库代码、不运行构建。
- 有界：默认最多 400 个源码文件，单文件 512 KiB；超出部分不进入图。
- 遍历剪枝：`os.walk` 在进入目录前跳过 `.git`、`.venv`、`node_modules`、`dist`、`target` 等，避免为大目录付出无意义 stat 成本。
- 查询缓存：每种语言只编译一次 tree-sitter query 和 parser；实测本仓 400 文件从 2m40s 降到约 3.7s。
- 语法级：不做类型推断、重载解析、接口实现分派、宏展开。
- 降级：缺少某个 tree-sitter grammar 时该语言返回空图，不阻塞其他语言。
- 不支持：Java、C/C++、C#、Ruby、PHP、Swift 等暂未接入；需要时按同一 `_DEF_QUERIES` / `_CALL_QUERIES` 结构扩展。

## 6. 与 repo map / 语义索引的分工

| 能力 | 回答的问题 | 数据 |
|---|---|---|
| repo map | 仓库里有哪些文件、哪些顶层符号 | tree-sitter 符号名 + 文件行数 |
| symbol graph | 谁调用谁、影响面多大 | 语法级调用边 |
| 语义索引 | 哪段代码语义上接近问题描述 | BM25 + 可选向量 + 符号重排 |
| LSP | 语义定义、引用、重命名、诊断 | 语言服务器 |

四者不是替代关系；`symbol graph` 的价值是零执行、跨语言、能在改代码前快速给出有界答案。

## 7. 验证

```bash
uv run pytest tests/test_symbol_graph.py tests/test_p0_repo_map.py -q
```

覆盖：

- Python / TS / Go / Rust 符号提取；
- 同文件、跨文件调用边解析；
- `callers` / `callees` / `impact` 深度遍历；
- `find_symbol` 三种新模式；
- `repo_map` 对 Go/Rust 的符号输出。
