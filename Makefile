# Spark 常用命令（轻量入口）
# 用法：make <target>；没有 make 时直接运行 $(PY) -m <module>

WINDOWS_SHELL := $(shell uname -s 2>/dev/null)
ifneq ($(OS),Windows_NT)
ifneq (,$(findstring MINGW,$(WINDOWS_SHELL)))
OS := Windows_NT
endif
ifneq (,$(findstring MSYS,$(WINDOWS_SHELL)))
OS := Windows_NT
endif
ifneq (,$(findstring CYGWIN,$(WINDOWS_SHELL)))
OS := Windows_NT
endif
endif

ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
ifneq (,$(WINDOWS_SHELL))
BOOTSTRAP_PY := py -3
export USERPROFILE := $(shell cygpath -w "$$(env | sed -n 's/^HOME=//p')")
export USERNAME := $(shell env | sed -n 's/^HOME=//p' | sed 's#.*/##')
else
BOOTSTRAP_PY := python
endif
else
PY := .venv/bin/python
BOOTSTRAP_PY := python3
endif
PIP := $(PY) -m pip

.PHONY: install dev test lint build run doctor clean audit

## 安装
install:            ## 安装到 .venv（可编辑 + dev 依赖）
	$(BOOTSTRAP_PY) -m venv .venv
	$(PIP) install -e ".[dev]"

## 日常开发
dev:                ## 启动 Web 界面（工作目录 = 当前目录，本机免登录）
	$(PY) -m spark web --workdir .

test:               ## 全量测试
	$(PY) -m pytest -q

## P1-9: 依赖漏洞扫描（uv audit 读 uv.lock；pip-audit 扫已装环境）
audit:              ## 依赖漏洞扫描 — uv audit + pip-audit（检出漏洞即非零退出）
	@echo "== uv audit =="
	uv audit
	@echo "== pip-audit =="
	$(PY) -m pip_audit --progress-spinner off

lint:               ## ruff 静态检查（需已安装 dev 依赖）
	$(PY) -m ruff check src/spark tests

build:              ## 构建 wheel（输出到 dist/）
	$(PIP) install -q build && $(PY) -m build

run:                ## 无头模式运行：make run PROMPT="你的任务"
	$(PY) -m spark exec "$(PROMPT)"

doctor:             ## 环境自检（依赖/配置/工作目录）
	$(PY) -m spark doctor

## 清理
clean:              ## 删除构建与缓存产物
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
