# -*- mode: python ; coding: utf-8 -*-
# Spark — PyInstaller spec（目录形式，dist/spark/ 可整体拷贝分发）
# 用法：
#   Windows:  pyinstaller spark.spec --distpath dist --workpath build
#   Linux/macOS 同命令（产出对应平台二进制）
# 构建说明见 docs/INSTALL.md（中英双语）
# 打包对象是 src/spark（pip 发行与 pytest 实测的同一棵树）。

import importlib.util
from pathlib import Path

# litellm 在 import 时通过 importlib.resources 读取包内 JSON 数据（模型价格表、
# tokenizer 词表、endpoints 配置等），PyInstaller 静态分析覆盖不到这类资源读取，
# 冻结后会 FileNotFoundError；这里在 spec 解析期递归收集全部 JSON + tokenizers 包。
_litellm_dir = Path(importlib.util.find_spec("litellm").origin).parent
_LITELLM_DATA = []
for _p in sorted(_litellm_dir.rglob("*.json")):
    _rel = _p.parent.relative_to(_litellm_dir)
    _dest = "litellm" if str(_rel) == "." else f"litellm/{_rel.as_posix()}"
    _LITELLM_DATA.append((str(_p), _dest))
_LITELLM_TOKENIZERS = _litellm_dir / "litellm_core_utils" / "tokenizers"
if _LITELLM_TOKENIZERS.is_dir():
    _LITELLM_DATA.append(
        (str(_LITELLM_TOKENIZERS), "litellm/litellm_core_utils/tokenizers")
    )

# --- 企业控制面 + 办公工作台的数据文件 ---
# rapidocr 的 ONNX 模型 (~16MB) 与配置经包内相对路径读取，PyInstaller 静态
# 分析看不到；缺了它们打包版的图片/扫描 PDF 导入会在运行时崩。
_OPTIONAL_DATA = []
for _mod, _patterns in (
    ("rapidocr_onnxruntime", ("*.onnx", "*.yaml", "*.txt")),
    ("reportlab", ("*.ttf", "*.afm", "*.pfb", "*.txt")),
):
    _spec = importlib.util.find_spec(_mod)
    if not _spec or not _spec.origin:
        continue
    _root = Path(_spec.origin).parent
    for _pattern in _patterns:
        for _p in _root.rglob(_pattern):
            if not _p.is_file():
                continue
            _rel = _p.relative_to(_root).parent
            _dest = _mod if str(_rel) == "." else f"{_mod}/{_rel.as_posix()}"
            _OPTIONAL_DATA.append((str(_p), _dest))

# rfc3987_syntax 经 importlib.resources 读取 .lark 语法文件（Lark 语法定义），
# PyInstaller 静态分析覆盖不到，冻结后 FileNotFoundError；递归收集整个包（排除 .pyc）。
# 该包并非所有平台都随依赖树安装（find_spec 可能为 None），须 None 安全。
_rfc3987_spec = importlib.util.find_spec("rfc3987_syntax")
if _rfc3987_spec is not None and _rfc3987_spec.origin:
    _rfc3987_dir = Path(_rfc3987_spec.origin).parent
    for _p in sorted(_rfc3987_dir.rglob("*")):
        if _p.is_file() and not _p.name.endswith(".pyc"):
            _rel = _p.parent.relative_to(_rfc3987_dir)
            _dest = "rfc3987_syntax" if str(_rel) == "." else f"rfc3987_syntax/{_rel.as_posix()}"
            _LITELLM_DATA.append((str(_p), _dest))

a = Analysis(
    ['src/spark/__main__.py'],
    pathex=[],
    binaries=[],
    datas=_OPTIONAL_DATA + [
        ('src/spark/web/dist', 'spark/web/dist'),  # 前端构建产物（仅 dist，不含 node_modules，控制体积）
        # Casbin 策略随包分发；缺少它企业控制面启动即失败（authz.py 按模块路径查找）。
        ('src/spark/control/policies', 'spark/control/policies'),
        ('src/spark/prompts', 'spark/prompts'),    # 系统提示词模板
        ('assets/logo.svg', 'assets'),             # 品牌 logo
        *_LITELLM_DATA,                            # litellm 运行时数据（importlib.resources 读取）
    ],
    hiddenimports=[
        # CLI / 配置 / 序列化
        'typer',
        'click',
        'pydantic',
        'pydantic_settings',
        'shellingham',
        # 网络（provider / MCP bridge）
        'httpx',
        'httpx._transports',
        'anyio',
        'sniffio',
        'certifi',
        'httpcore',
        'h11',
        # TUI（textual + rich）
        'textual',
        'textual.app',
        'textual.widgets',
        'rich',
        'rich.markup',
        # LLM / 检索栈（PyInstaller 动态导入多，显式兜底）
        'litellm',
        'chromadb',
        'chromadb.api',
        'chromadb.api.segment',
        # chromadb 的 Settings 用配置项动态解析组件类（import_module），静态分析看不到；
        # 默认实现全部显式声明，含 Rust 绑定扩展
        'chromadb.api.rust',
        'chromadb_rust_bindings',
        'chromadb.db.impl.sqlite',
        'chromadb.execution.executor.local',
        'chromadb.segment.impl.manager.local',
        'chromadb.segment.impl.distributed.segment_directory',
        'chromadb.quota.simple_quota_enforcer',
        'chromadb.rate_limit.simple_rate_limit',
        'chromadb.ingest.impl.simple_policy',
        'chromadb.telemetry.product.posthog',
        'llama_index.core',
        'langgraph',
        'langgraph.graph',
        # spark 内部延迟导入（桌面壳 / Web / 技能 / ACP）
        'spark.web',
        'spark.web.server',
        'spark.web.routes',
        'spark.web._handlers',
        'spark.web._routing',
        'spark.web.state',
        'spark.web.sse',
        'spark.web.utils',
        'spark.desktop',
        'spark.control',
        'spark.control.app',
        'spark.control.ai',
        'spark.control.documents',
        'spark.control.email_imap',
        'spark.control.pdf',
        'spark.adapters',
        'fastapi', 'uvicorn', 'sqlalchemy', 'alembic', 'casbin',
        'authlib', 'jwt', 'multipart', 'reportlab',
        'docx', 'openpyxl', 'pptx', 'pypdf', 'pypdfium2',
        'rapidocr_onnxruntime', 'extract_msg',
        'spark.tools',
        'spark.tools.web',
        # tree-sitter 语法包在 repo_map.py / wiki.py 里是函数内导入，
        # PyInstaller 静态分析看不到；缺了它们打包版的 `spark wiki`
        # 会静默降级成 0 个符号（不报错，只是文档空白）。
        'tree_sitter',
        'tree_sitter_python',
        'tree_sitter_javascript',
        'tree_sitter_typescript',
        'spark.acp_server',
        'acp',
        # litellm 资源目录（空 __init__.py + JSON 数据，仅经 importlib.resources 触达）
        'litellm.litellm_core_utils.tokenizers',
        # 桌面壳（可选依赖，安装后打进二进制；未装则由运行时报缺失）
        'webview',
        'clr',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'pytest',
        'pytest_asyncio',
        'PySide6',
        'PyQt5',
        'PyQt6',
        'IPython',
        'matplotlib',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='spark',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,          # CLI + 可打印启动 URL
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/logo.ico' if os.path.exists('assets/logo.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='spark',
)
