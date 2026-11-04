# 安装与打包（Windows EXE / macOS / Linux）

## 方式 0 — 一键安装（推荐普通用户）

Linux / macOS（自动取最新 Release，校验 SHA256，装到 `~/.local/share/spark`）：

```bash
curl -fsSL https://raw.githubusercontent.com/badhope/spark/main/scripts/install.sh | sh
```

Windows PowerShell（装到 `%LOCALAPPDATA%\Spark`，自动加用户 PATH）：

```powershell
irm https://raw.githubusercontent.com/badhope/spark/main/scripts/install.ps1 | iex
```

两者都支持：

| 环境变量 | 作用 |
|---|---|
| `SPARK_VERSION` | 固定版本，例如 `v0.9.12` |
| `SPARK_MIRROR` | 内网镜像根地址（需同时提供产物与 `SHA256SUMS`） |
| `SPARK_INSTALL_DIR` | 覆盖安装目录 |

**校验不是可选项**：Release 缺少 `SHA256SUMS` 或哈希不符时脚本直接中止。
装完升级：

```bash
spark update --check     # 只看有没有新版
spark update             # 下载 → 校验 → 原子替换（旧版备份为 *.old）
```

`spark update` 只对打包版有效；pip/源码安装用 `pip install -U spark-agent`。
不想被检查打扰可在 `config.toml` 设 `[updater] mode = "off"`。

## 方式 0½ — scoop（Windows 包管理器用户）

本仓库自带 scoop bucket（`bucket/` 目录），两种装法任选：

```powershell
# 直接按 URL 安装（无需添加 bucket）
scoop install https://raw.githubusercontent.com/badhope/spark/main/bucket/spark.json

# 或把本仓库加为 bucket，之后可用 scoop update* 跟随版本
scoop bucket add spark https://github.com/badhope/spark
scoop install spark/spark
```

manifest（`bucket/spark.json`）带 `autoupdate`：新 Release 发布后
`scoop update spark` 即可升级，哈希自动取自 GitHub Release API 的
asset digest，无需手工维护。manifest 同时注册 `spark` 与
`spark-desktop` 两个命令。卸载用 `scoop uninstall spark`；清理
Spark 的本地数据（会话/配置/记忆）用 `spark uninstall`。


## 方式 A — 源码运行（最快）

```bash
git clone https://gitcode.com/badhope/spark.git
cd spark
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
spark web                        # 打开打印出的地址
```

## 方式 B — 打包版二进制

### Windows EXE

任意 Windows 机器（Python 3.11+）本地一键构建：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
# 产物：dist\spark\spark.exe
dist\spark\spark.exe web
```

或直接下载 **GitHub Releases** 现成包：打个 tag（`git tag v0.9.0 && git push --tags`），
`Release` 工作流会自动构建 `spark-windows-x86_64.zip`（同时产出 Linux/macOS 压缩包）。

### Linux / macOS

```bash
bash scripts/build.sh            # 产物：dist/spark/spark
./dist/spark/spark web
```

### 桌面版（原生窗口）

另有一份 GUI 产物，双击直接开窗口，不经过终端：

```powershell
pyinstaller spark-desktop.spec --distpath dist --workpath build
# 产物：dist\spark-desktop\spark-desktop.exe
```

两者的区别：

| | `spark.exe` | `spark-desktop.exe` |
|---|---|---|
| Windows 子系统 | Console（3） | GUI（2，无黑框） |
| 双击行为 | 进 TUI | 直接开原生窗口 |
| 入口模块 | `spark.__main__` | `spark.desktop_main` |
| 适用 | 命令行 / 服务器 / 脚本 | 普通用户日常使用 |

桌面版启动失败时会写 `~/.spark/desktop-crash.log`（GUI 子系统没有 stderr
可看，不写日志的话用户只会觉得「点了没反应」）。

### 打包内容

- `spark` 可执行文件 —— CLI + Web 服务（前端资源已内嵌）
- `spark-desktop` 可执行文件 —— 同一套后端 + pywebview 原生窗口
- 全部数据本地化，运行于 `~/.spark/`
- 无需额外浏览器依赖，浏览器打开打印的 `http://127.0.0.1:端口` 即可（服务默认仅监听 `127.0.0.1`（本机访问；如需外部预览请显式 `spark web --host 0.0.0.0`，并自行承担暴露风险））
- 可选能力（MCP / 嵌入 / TUI）缺失时优雅降级

## 首次使用

1. 打开打印的 `http://127.0.0.1:端口`（默认仅本机可访问；外部预览需显式 `--host 0.0.0.0`）
2. 设置 → 选模型预设（DeepSeek / 通义 / 智谱 / Kimi / 豆包 / Agnes AI / Ollama / 自定义），粘贴 API Key
3. 把「工作目录」指向你的项目
4. 新建会话开干 —— 每次写入与命令都在审批门管控下

## 常见问题

- 端口被占 → `spark web --port 8788`
- 模型报错 → 设置面板「测试连接」；检查 `base_url` / `model` / `api_key`
- 需要代理 → 配置 `proxy` 或设 `HTTPS_PROXY` 环境变量
- Windows SmartScreen 提示 → 「更多信息 → 仍要运行」（未签名构建）

## 卸载

```bash
spark uninstall --list   # 只看将删除的清单，不动任何文件
spark uninstall          # 列清单 → 确认 → 清掉 ~/.spark 全部数据
spark uninstall --yes    # 跳过确认
```

清的是数据目录（配置 / 会话 / 向量记忆 / 知识库 / 检查点 / 备份）；
Python 包本身用 `pip uninstall spark-agent`，scoop 安装的用
`scoop uninstall spark`。各项目内的 `.spark/` 工作区文件不受影响。
安全守卫：`SPARK_HOME` 指向家目录本身或盘符根时拒绝执行。

> AI生成

### VS Code 扩展

仓库自带扩展（`vscode-spark/`），三条命令把工作区接入 Spark：
**Open Web UI**（自动起服务、解析端口、复用实例）、**New TUI Session**、
**Ask about selection**（选中代码带上下文提问）。本地 VSIX 安装：

```bash
code --install-extension vscode-spark/spark-agent-0.12.0.vsix
```

前提：已安装 Spark（打包版或 `pip install spark-agent`）；审批模式在
扩展设置里选（默认 `suggest`）。

### 截图自动化（维护者）

`python scripts/screenshots.py` 用本机 Edge/Chrome headless 重拍
`assets/screenshots/` 里的真实 UI 截图（起真实 mock 服务 + 预置演示
会话）；UI 改版后跑一次即可让 README 的图保持真实。
