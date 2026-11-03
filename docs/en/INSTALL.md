# Install & Build (Windows EXE / macOS / Linux)

## Option A — run from source (fastest)

```bash
git clone https://gitcode.com/badhope/spark.git
cd spark
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
spark web                        # open the printed URL
```

## Option B — packaged binary

### Windows EXE

Local build (any Windows machine with Python 3.11+):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
# output: dist\spark\spark.exe
dist\spark\spark.exe web
```

Or grab the ready-made artifact from **GitHub Releases** — tag a version
(`git tag v0.9.0 && git push --tags`) and the `Release` workflow builds
`spark-windows-x86_64.zip` (plus Linux/macOS archives) automatically.

### Linux / macOS

```bash
bash scripts/build.sh            # output: dist/spark/spark
./dist/spark/spark web
```

### What the package contains

- `spark` executable — CLI + Web server (frontend assets embedded)
- Everything runs locally under `~/.spark/`
- No runtime dependency on a browser other than opening the printed `http://127.0.0.1:PORT` (the server listens on `127.0.0.1` only by default; pass `spark web --host 0.0.0.0` explicitly for external preview, at your own risk)
- Optional extras (MCP / embedding / TUI) degrade gracefully when absent

## First run

1. Open the printed `http://127.0.0.1:PORT` (loopback-only by default; external preview needs an explicit `--host 0.0.0.0`)
2. Settings → pick a model preset (DeepSeek / Qwen / GLM / Kimi / Doubao / Agnes AI / Ollama / custom), paste the API key
3. Point the working directory at your project
4. Create a session and start — every write and command is gated by approval

## Troubleshooting

- Port busy → `spark web --port 8788`
- Model errors → Settings → Test connection; check `base_url` / `model` / `api_key`
- Need a proxy → set `proxy` in config or `HTTPS_PROXY` env var
- Windows SmartScreen → click "More info → Run anyway" (unsigned build)

### Windows package manager (scoop)

The repo ships its own scoop bucket (`bucket/`):

```powershell
scoop install https://raw.githubusercontent.com/badhope/spark/main/bucket/spark.json
# or
scoop bucket add spark https://github.com/badhope/spark
scoop install spark/spark
```

The manifest has `autoupdate`; hashes come from the GitHub Release API asset digest.

## Uninstall

```bash
spark uninstall --list   # print what would be deleted, touch nothing
spark uninstall          # list → confirm → wipe ~/.spark
spark uninstall --yes    # skip confirmation
```

This clears the data directory (config / sessions / vector memory /
knowledge / checkpoints / backups). The Python package itself is removed
with `pip uninstall spark-agent`, scoop installs with
`scoop uninstall spark`. Per-project `.spark/` workspace files are not
touched. A safety guard refuses to run when `SPARK_HOME` points at the
user's home directory or a drive root.

> AI生成
