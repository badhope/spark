"""Thirty self-contained, runnable competitive eval tasks.

These are not benchmark leaderboard clones and they do not pretend to be
SWE-bench.  They are a stable internal yardstick: every task has a tiny repo,
a failing check, and a known gold state.  A real provider must make the repo
pass without seeing the gold files; the fixture validator uses the gold state
only to prove the task itself is sound.

Categories deliberately cover what the product sells:
bugfix, feature, refactor, test-authoring, and code retrieval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from textwrap import dedent


@dataclass(frozen=True)
class TaskSpec:
    id: str
    category: str
    prompt: str
    files: dict[str, str]
    check: str
    gold: dict[str, str] = field(default_factory=dict)
    check_contains: tuple[str, ...] = ()
    timeout_sec: int = 90
    tags: tuple[str, ...] = ()


def _t(*parts: str) -> str:
    return dedent("\n".join(parts)).lstrip("\n")


def builtin_competitive_tasks() -> list[TaskSpec]:
    tasks: list[TaskSpec] = []

    # ---------------------------------------------------------------- bugfix
    tasks.append(TaskSpec(
        id="bugfix-calc-add",
        category="bugfix",
        prompt="calc.py 的 add() 把加法写成了减法。修复它并运行 test_calc.py。",
        files={
            "calc.py": _t(
                '"""Small math helpers."""',
                "",
                "def add(a, b):",
                "    # BUG: should be addition",
                "    return a - b",
            ),
            "test_calc.py": _t(
                "from calc import add",
                "",
                "def test_add_positive():",
                "    assert add(2, 3) == 5",
                "",
                "def test_add_negative():",
                "    assert add(-1, 1) == 0",
            ),
        },
        check="{python} -m pytest -q test_calc.py",
        gold={"calc.py": _t('"""Small math helpers."""', "", "def add(a, b):", "    return a + b")},
        tags=("single-file", "python"),
    ))

    tasks.append(TaskSpec(
        id="bugfix-mean-empty",
        category="bugfix",
        prompt="stats.py 的 mean() 在单元素和空列表上返回错误。修复边界条件并运行测试。",
        files={
            "stats.py": _t(
                '"""Stats helpers."""',
                "",
                "def mean(values):",
                "    if len(values) > 1:",
                "        return sum(values) / len(values)",
                "    return 0",
            ),
            "test_stats.py": _t(
                "from stats import mean",
                "",
                "def test_mean_multi():",
                "    assert mean([1, 2, 3]) == 2",
                "",
                "def test_mean_single():",
                "    assert mean([5]) == 5",
                "",
                "def test_mean_empty():",
                "    assert mean([]) == 0",
            ),
        },
        check="{python} -m pytest -q test_stats.py",
        gold={"stats.py": _t('"""Stats helpers."""', "", "def mean(values):", "    if not values:", "        return 0", "    return sum(values) / len(values)")},
    ))

    tasks.append(TaskSpec(
        id="bugfix-slugify",
        category="bugfix",
        prompt="slugify() 只把空格换成连字符，没有转小写、没有合并重复分隔符。修复并运行测试。",
        files={
            "slug.py": _t(
                '"""URL slug helper."""',
                "",
                "def slugify(text):",
                "    return text.replace(' ', '-')",
            ),
            "test_slug.py": _t(
                "from slug import slugify",
                "",
                "def test_basic():",
                "    assert slugify('Hello World') == 'hello-world'",
                "",
                "def test_collapse():",
                "    assert slugify('A  B') == 'a-b'",
                "",
                "def test_strip():",
                "    assert slugify('  Hello  ') == 'hello'",
            ),
        },
        check="{python} -m pytest -q test_slug.py",
        gold={"slug.py": _t('"""URL slug helper."""', "", "import re", "", "def slugify(text):", "    text = text.strip().lower()", "    text = re.sub(r'[^a-z0-9]+', '-', text)", "    return text.strip('-')")},
    ))

    tasks.append(TaskSpec(
        id="bugfix-parse-bool",
        category="bugfix",
        prompt="parse_bool() 只认字面量 'true'，请支持 true/false/yes/no/1/0 并忽略大小写。",
        files={
            "flags.py": _t(
                '"""Boolean flag parser."""',
                "",
                "def parse_bool(value):",
                "    return value == 'true'",
            ),
            "test_flags.py": _t(
                "from flags import parse_bool",
                "",
                "def test_true_words():",
                "    assert parse_bool('TRUE') is True",
                "    assert parse_bool('yes') is True",
                "    assert parse_bool('1') is True",
                "",
                "def test_false_words():",
                "    assert parse_bool('false') is False",
                "    assert parse_bool('no') is False",
                "    assert parse_bool('0') is False",
            ),
        },
        check="{python} -m pytest -q test_flags.py",
        gold={"flags.py": _t('"""Boolean flag parser."""', "", "def parse_bool(value):", "    return str(value).strip().lower() in {'true', 'yes', '1', 'on'}")},
    ))

    tasks.append(TaskSpec(
        id="bugfix-chunk-tail",
        category="bugfix",
        prompt="chunk() 会丢掉最后一个不足 size 的尾块。修复并运行测试。",
        files={
            "chunks.py": _t(
                '"""List chunking."""',
                "",
                "def chunk(items, size):",
                "    return [items[i:i + size] for i in range(0, len(items) - size + 1, size)]",
            ),
            "test_chunks.py": _t(
                "from chunks import chunk",
                "",
                "def test_exact():",
                "    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]",
                "",
                "def test_tail():",
                "    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]",
            ),
        },
        check="{python} -m pytest -q test_chunks.py",
        gold={"chunks.py": _t('"""List chunking."""', "", "def chunk(items, size):", "    if size <= 0:", "        raise ValueError('size must be positive')", "    return [items[i:i + size] for i in range(0, len(items), size)]")},
    ))

    tasks.append(TaskSpec(
        id="bugfix-retry-attempts",
        category="bugfix",
        prompt="retry() 传入 attempts=3 时只尝试 2 次。修复为最多尝试 attempts 次，并在成功时立即返回。",
        files={
            "retry.py": _t(
                '"""Tiny retry helper."""',
                "",
                "def retry(fn, attempts=3):",
                "    last = None",
                "    for _ in range(attempts - 1):",
                "        try:",
                "            return fn()",
                "        except Exception as exc:",
                "            last = exc",
                "    raise last",
            ),
            "test_retry.py": _t(
                "from retry import retry",
                "",
                "def test_third_time_succeeds():",
                "    calls = {'n': 0}",
                "    def flaky():",
                "        calls['n'] += 1",
                "        if calls['n'] < 3:",
                "            raise RuntimeError('not yet')",
                "        return 'ok'",
                "    assert retry(flaky, attempts=3) == 'ok'",
            ),
        },
        check="{python} -m pytest -q test_retry.py",
        gold={"retry.py": _t('"""Tiny retry helper."""', "", "def retry(fn, attempts=3):", "    last = None", "    for _ in range(attempts):", "        try:", "            return fn()", "        except Exception as exc:", "            last = exc", "    raise last")},
    ))

    tasks.append(TaskSpec(
        id="bugfix-csv-header",
        category="bugfix",
        prompt="to_csv() 忘记写表头，导致输出无法被 csv.DictReader 读取。补表头并运行测试。",
        files={
            "table.py": _t(
                '"""CSV serialization."""',
                "",
                "def to_csv(rows):",
                "    return '\\n'.join(','.join(str(row[key]) for key in ('name', 'age')) for row in rows)",
            ),
            "test_table.py": _t(
                "import csv",
                "import io",
                "from table import to_csv",
                "",
                "def test_header_and_rows():",
                "    text = to_csv([{'name': 'Ada', 'age': 36}])",
                "    assert text.splitlines()[0] == 'name,age'",
                "    assert list(csv.DictReader(io.StringIO(text))) == [{'name': 'Ada', 'age': '36'}]",
            ),
        },
        check="{python} -m pytest -q test_table.py",
        gold={"table.py": _t('"""CSV serialization."""', "", "def to_csv(rows):", "    header = 'name,age'", "    body = ['%s,%s' % (row['name'], row['age']) for row in rows]", "    return '\\n'.join([header, *body])")},
    ))

    tasks.append(TaskSpec(
        id="bugfix-date-order",
        category="bugfix",
        prompt="parse_date() 把 ISO 日期的年月日顺序解析反了。修复并运行测试。",
        files={
            "dates.py": _t(
                '"""Date parsing."""',
                "",
                "def parse_date(text):",
                "    month, day, year = text.split('-')",
                "    return int(year), int(month), int(day)",
            ),
            "test_dates.py": _t(
                "from dates import parse_date",
                "",
                "def test_iso():",
                "    assert parse_date('2026-11-23') == (2026, 11, 23)",
            ),
        },
        check="{python} -m pytest -q test_dates.py",
        gold={"dates.py": _t('"""Date parsing."""', "", "def parse_date(text):", "    year, month, day = text.split('-')", "    return int(year), int(month), int(day)")},
    ))

    tasks.append(TaskSpec(
        id="bugfix-cache-key",
        category="bugfix",
        prompt="memo() 的缓存键忽略了参数，f(1) 之后 f(2) 会拿到错误结果。修复并运行测试。",
        files={
            "memo.py": _t(
                '"""Memoization helper."""',
                "",
                "def memo(fn):",
                "    cache = {}",
                "    def wrapper(*args):",
                "        if 'value' not in cache:",
                "            cache['value'] = fn(*args)",
                "        return cache['value']",
                "    return wrapper",
            ),
            "test_memo.py": _t(
                "from memo import memo",
                "",
                "def test_distinct_args():",
                "    calls = {'n': 0}",
                "    @memo",
                "    def double(x):",
                "        calls['n'] += 1",
                "        return x * 2",
                "    assert double(1) == 2",
                "    assert double(2) == 4",
                "    assert double(1) == 2",
                "    assert calls['n'] == 2",
            ),
        },
        check="{python} -m pytest -q test_memo.py",
        gold={"memo.py": _t('"""Memoization helper."""', "", "def memo(fn):", "    cache = {}", "    def wrapper(*args):", "        if args not in cache:", "            cache[args] = fn(*args)", "        return cache[args]", "    return wrapper")},
    ))

    tasks.append(TaskSpec(
        id="bugfix-path-escape",
        category="bugfix",
        prompt="safe_join() 遇到绝对路径会跳出 base 目录。请拒绝绝对路径和 .. 逃逸，并运行测试。",
        files={
            "paths.py": _t(
                '"""Path helpers."""',
                "",
                "import os",
                "",
                "def safe_join(base, name):",
                "    return os.path.join(base, name)",
            ),
            "test_paths.py": _t(
                "import pytest",
                "from paths import safe_join",
                "",
                "def test_relative_ok():",
                "    assert safe_join('/tmp/base', 'x.txt') == '/tmp/base/x.txt'",
                "",
                "def test_absolute_rejected():",
                "    with pytest.raises(ValueError):",
                "        safe_join('/tmp/base', '/etc/passwd')",
                "",
                "def test_parent_rejected():",
                "    with pytest.raises(ValueError):",
                "        safe_join('/tmp/base', '../x')",
            ),
        },
        check="{python} -m pytest -q test_paths.py",
        gold={"paths.py": _t('"""Path helpers."""', "", "import os", "", "def safe_join(base, name):", "    if os.path.isabs(name) or '..' in name.split('/'):", "        raise ValueError('path escapes base')", "    return os.path.join(base, name)")},
    ))

    # --------------------------------------------------------------- feature
    tasks.append(TaskSpec(
        id="feature-pagination",
        category="feature",
        prompt="为 paginate() 实现 1-based 分页：page=1 返回第一页；越界返回空列表。运行测试。",
        files={
            "paging.py": _t(
                '"""Pagination helper."""',
                "",
                "def paginate(items, page, size):",
                "    return items[page * size:(page + 1) * size]",
            ),
            "test_paging.py": _t(
                "from paging import paginate",
                "",
                "def test_first_page():",
                "    assert paginate(list(range(5)), 1, 2) == [0, 1]",
                "",
                "def test_second_page():",
                "    assert paginate(list(range(5)), 2, 2) == [2, 3]",
                "",
                "def test_out_of_range():",
                "    assert paginate(list(range(5)), 9, 2) == []",
            ),
        },
        check="{python} -m pytest -q test_paging.py",
        gold={"paging.py": _t('"""Pagination helper."""', "", "def paginate(items, page, size):", "    if page < 1 or size < 1:", "        return []", "    start = (page - 1) * size", "    return items[start:start + size]")},
    ))

    tasks.append(TaskSpec(
        id="feature-email-validation",
        category="feature",
        prompt="实现 validate_email()：拒绝空格、多个 @、缺少域名点号和空本地部分。运行测试。",
        files={
            "email_validator.py": _t(
                '"""Email validation."""',
                "",
                "def validate_email(value):",
                "    return '@' in value",
            ),
            "test_email.py": _t(
                "from email_validator import validate_email",
                "",
                "def test_valid():",
                "    assert validate_email('a@example.com')",
                "",
                "def test_invalid():",
                "    assert not validate_email('a b@example.com')",
                "    assert not validate_email('a@@example.com')",
                "    assert not validate_email('a@example')",
                "    assert not validate_email('@example.com')",
            ),
        },
        check="{python} -m pytest -q test_email.py",
        gold={"email_validator.py": _t('"""Email validation."""', "", "import re", "", "def validate_email(value):", "    if not value or ' ' in value:", "        return False", "    return bool(re.fullmatch(r'[^@]+@[^@]+\\.[^@]+', value))")},
    ))

    tasks.append(TaskSpec(
        id="feature-token-bucket",
        category="feature",
        prompt="实现 TokenBucket.allow(now)：容量 3，每秒补 1 个 token；同一秒内第 4 次应拒绝。运行测试。",
        files={
            "bucket.py": _t(
                '"""Rate limit bucket."""',
                "",
                "class TokenBucket:",
                "    def __init__(self, capacity=3, rate=1.0):",
                "        self.capacity = capacity",
                "        self.rate = rate",
                "        self.tokens = capacity",
                "        self.last = 0.0",
                "",
                "    def allow(self, now):",
                "        return True",
            ),
            "test_bucket.py": _t(
                "from bucket import TokenBucket",
                "",
                "def test_burst_then_refill():",
                "    bucket = TokenBucket()",
                "    assert all(bucket.allow(0.0) for _ in range(3))",
                "    assert bucket.allow(0.0) is False",
                "    assert bucket.allow(1.0) is True",
            ),
        },
        check="{python} -m pytest -q test_bucket.py",
        gold={"bucket.py": _t('"""Rate limit bucket."""', "", "class TokenBucket:", "    def __init__(self, capacity=3, rate=1.0):", "        self.capacity = capacity", "        self.rate = rate", "        self.tokens = float(capacity)", "        self.last = 0.0", "", "    def allow(self, now):", "        elapsed = max(0.0, now - self.last)", "        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)", "        self.last = now", "        if self.tokens >= 1:", "            self.tokens -= 1", "            return True", "        return False")},
    ))

    tasks.append(TaskSpec(
        id="feature-env-config",
        category="feature",
        prompt="实现 load_config(env)：默认 debug=False；DEBUG=1/true 为 True；PORT 转 int；缺失 PORT 用 8000。",
        files={
            "config.py": _t(
                '"""Config loader."""',
                "",
                "def load_config(env):",
                "    return {'debug': False, 'port': 8000}",
            ),
            "test_config.py": _t(
                "from config import load_config",
                "",
                "def test_defaults():",
                "    assert load_config({}) == {'debug': False, 'port': 8000}",
                "",
                "def test_overrides():",
                "    assert load_config({'DEBUG': 'true', 'PORT': '9000'}) == {'debug': True, 'port': 9000}",
            ),
        },
        check="{python} -m pytest -q test_config.py",
        gold={"config.py": _t('"""Config loader."""', "", "def load_config(env):", "    debug = str(env.get('DEBUG', '')).lower() in {'1', 'true', 'yes', 'on'}", "    port = int(env.get('PORT', '8000'))", "    return {'debug': debug, 'port': port}")},
    ))

    tasks.append(TaskSpec(
        id="feature-argparse-cli",
        category="feature",
        prompt="给 greet() 的 CLI 增加 --name 参数和 --shout 开关；默认 Hello, world!；--shout 全大写。运行测试。",
        files={
            "cli.py": _t(
                '"""Tiny CLI."""',
                "",
                "import argparse",
                "",
                "def build_parser():",
                "    return argparse.ArgumentParser()",
                "",
                "def greet(args):",
                "    return 'Hello, world!'",
            ),
            "test_cli.py": _t(
                "from cli import build_parser, greet",
                "",
                "def test_default():",
                "    assert greet(build_parser().parse_args([])) == 'Hello, world!'",
                "",
                "def test_name_and_shout():",
                "    args = build_parser().parse_args(['--name', 'Ada', '--shout'])",
                "    assert greet(args) == 'HELLO, ADA!'",
            ),
        },
        check="{python} -m pytest -q test_cli.py",
        gold={"cli.py": _t('"""Tiny CLI."""', "", "import argparse", "", "def build_parser():", "    parser = argparse.ArgumentParser()", "    parser.add_argument('--name', default='world')", "    parser.add_argument('--shout', action='store_true')", "    return parser", "", "def greet(args):", "    text = f'Hello, {args.name}!'", "    return text.upper() if args.shout else text")},
    ))

    tasks.append(TaskSpec(
        id="feature-jsonl",
        category="feature",
        prompt="实现 append_jsonl(path, obj) 和 read_jsonl(path)：每行一个 JSON，追加写，空文件读为空列表。",
        files={
            "jsonl.py": _t(
                '"""JSONL helpers."""',
                "",
                "import json",
                "",
                "def append_jsonl(path, obj):",
                "    with open(path, 'w', encoding='utf-8') as fh:",
                "        fh.write(json.dumps(obj))",
                "",
                "def read_jsonl(path):",
                "    return []",
            ),
            "test_jsonl.py": _t(
                "from jsonl import append_jsonl, read_jsonl",
                "",
                "def test_append_and_read(tmp_path):",
                "    path = tmp_path / 'x.jsonl'",
                "    append_jsonl(path, {'a': 1})",
                "    append_jsonl(path, {'a': 2})",
                "    assert read_jsonl(path) == [{'a': 1}, {'a': 2}]",
                "",
                "def test_empty(tmp_path):",
                "    assert read_jsonl(tmp_path / 'missing.jsonl') == []",
            ),
        },
        check="{python} -m pytest -q test_jsonl.py",
        gold={"jsonl.py": _t('"""JSONL helpers."""', "", "import json", "import os", "", "def append_jsonl(path, obj):", "    with open(path, 'a', encoding='utf-8') as fh:", "        fh.write(json.dumps(obj, ensure_ascii=False) + '\\n')", "", "def read_jsonl(path):", "    if not os.path.exists(path):", "        return []", "    with open(path, encoding='utf-8') as fh:", "        return [json.loads(line) for line in fh if line.strip()]")},
    ))

    tasks.append(TaskSpec(
        id="feature-search-casefold",
        category="feature",
        prompt="search(items, query) 现在区分大小写。改成不区分大小写的子串匹配，空查询返回全部。",
        files={
            "search.py": _t(
                '"""Search helper."""',
                "",
                "def search(items, query):",
                "    if not query:",
                "        return []",
                "    return [item for item in items if query in item]",
            ),
            "test_search.py": _t(
                "from search import search",
                "",
                "def test_casefold():",
                "    items = ['Alpha', 'beta', 'GAMMA']",
                "    assert search(items, 'ALP') == ['Alpha']",
                "    assert search(items, 'ga') == ['GAMMA']",
                "",
                "def test_empty():",
                "    assert search(['a'], '') == ['a']",
            ),
        },
        check="{python} -m pytest -q test_search.py",
        gold={"search.py": _t('"""Search helper."""', "", "def search(items, query):", "    needle = query.lower()", "    return [item for item in items if needle in item.lower()]")},
    ))

    tasks.append(TaskSpec(
        id="feature-csv-export",
        category="feature",
        prompt="实现 export_csv(rows, path)：按 name,age 列写 CSV，带表头，返回写入行数（不含表头）。",
        files={
            "export.py": _t(
                '"""CSV export."""',
                "",
                "def export_csv(rows, path):",
                "    return 0",
            ),
            "test_export.py": _t(
                "import csv",
                "from export import export_csv",
                "",
                "def test_export(tmp_path):",
                "    path = tmp_path / 'out.csv'",
                "    rows = [{'name': 'Ada', 'age': 36}, {'name': 'Bob', 'age': 40}]",
                "    assert export_csv(rows, path) == 2",
                "    with open(path, newline='', encoding='utf-8') as fh:",
                "        assert list(csv.DictReader(fh)) == [",
                "            {'name': 'Ada', 'age': '36'}, {'name': 'Bob', 'age': '40'}",
                "        ]",
            ),
        },
        check="{python} -m pytest -q test_export.py",
        gold={"export.py": _t('"""CSV export."""', "", "import csv", "", "def export_csv(rows, path):", "    with open(path, 'w', newline='', encoding='utf-8') as fh:", "        writer = csv.DictWriter(fh, fieldnames=['name', 'age'])", "        writer.writeheader()", "        for row in rows:", "            writer.writerow({'name': row['name'], 'age': row['age']})", "    return len(rows)")},
    ))

    tasks.append(TaskSpec(
        id="feature-log-redaction",
        category="feature",
        prompt="实现 redact(data)：递归脱敏 password、token、api_key 字段，返回新字典，不改原对象。",
        files={
            "redact.py": _t(
                '"""Log redaction."""',
                "",
                "def redact(data):",
                "    return data",
            ),
            "test_redact.py": _t(
                "from redact import redact",
                "",
                "def test_nested():",
                "    src = {'user': {'password': 'p', 'name': 'Ada'}, 'token': 't'}",
                "    out = redact(src)",
                "    assert out['user']['password'] == '[REDACTED]'",
                "    assert out['token'] == '[REDACTED]'",
                "    assert out['user']['name'] == 'Ada'",
                "    assert src['user']['password'] == 'p'",
            ),
        },
        check="{python} -m pytest -q test_redact.py",
        gold={"redact.py": _t('"""Log redaction."""', "", "SECRET_KEYS = {'password', 'token', 'api_key'}", "", "def redact(data):", "    if isinstance(data, dict):", "        return {key: ('[REDACTED]' if key.lower() in SECRET_KEYS else redact(value)) for key, value in data.items()}", "    if isinstance(data, list):", "        return [redact(item) for item in data]", "    return data")},
    ))

    tasks.append(TaskSpec(
        id="feature-plugin-registry",
        category="feature",
        prompt="实现 Registry.resolve(name)：先精确匹配，再按别名匹配；未知返回 None。运行测试。",
        files={
            "registry.py": _t(
                '"""Plugin registry."""',
                "",
                "class Registry:",
                "    def __init__(self):",
                "        self.items = {}",
                "        self.aliases = {}",
                "",
                "    def add(self, name, value, aliases=()):",
                "        self.items[name] = value",
                "        for alias in aliases:",
                "            self.aliases[alias] = name",
                "",
                "    def resolve(self, name):",
                "        return None",
            ),
            "test_registry.py": _t(
                "from registry import Registry",
                "",
                "def test_resolve():",
                "    reg = Registry()",
                "    reg.add('formatter', 'F', aliases=('fmt',))",
                "    assert reg.resolve('formatter') == 'F'",
                "    assert reg.resolve('fmt') == 'F'",
                "    assert reg.resolve('missing') is None",
            ),
        },
        check="{python} -m pytest -q test_registry.py",
        gold={"registry.py": _t('"""Plugin registry."""', "", "class Registry:", "    def __init__(self):", "        self.items = {}", "        self.aliases = {}", "", "    def add(self, name, value, aliases=()):", "        self.items[name] = value", "        for alias in aliases:", "            self.aliases[alias] = name", "", "    def resolve(self, name):", "        if name in self.items:", "            return self.items[name]", "        target = self.aliases.get(name)", "        return self.items.get(target) if target else None")},
    ))

    # --------------------------------------------------------------- refactor
    tasks.append(TaskSpec(
        id="refactor-extract-normalize",
        category="refactor",
        prompt="把重复的 strip/lower 逻辑抽成 normalize()，保持行为不变，并让两个入口都调用它。",
        files={
            "text.py": _t(
                '"""Text entrypoints."""',
                "",
                "def username(value):",
                "    return value.strip().lower()",
                "",
                "def email(value):",
                "    return value.strip().lower()",
            ),
            "test_text.py": _t(
                "from text import username, email, normalize",
                "",
                "def test_behavior():",
                "    assert normalize('  Ada ') == 'ada'",
                "    assert username('  Ada ') == 'ada'",
                "    assert email(' A@B.COM ') == 'a@b.com'",
            ),
        },
        check="{python} -m pytest -q test_text.py",
        gold={"text.py": _t('"""Text entrypoints."""', "", "def normalize(value):", "    return value.strip().lower()", "", "def username(value):", "    return normalize(value)", "", "def email(value):", "    return normalize(value)")},
    ))

    tasks.append(TaskSpec(
        id="refactor-point-dataclass",
        category="refactor",
        prompt="把 Point 从字典改成 dataclass；distance() 改用属性访问，保持输出。",
        files={
            "point.py": _t(
                '"""Point helpers."""',
                "",
                "def make_point(x, y):",
                "    return {'x': x, 'y': y}",
                "",
                "def distance(point):",
                "    return (point['x'] ** 2 + point['y'] ** 2) ** 0.5",
            ),
            "test_point.py": _t(
                "from point import make_point, distance",
                "",
                "def test_distance():",
                "    p = make_point(3, 4)",
                "    assert p.x == 3",
                "    assert p.y == 4",
                "    assert distance(p) == 5",
            ),
        },
        check="{python} -m pytest -q test_point.py",
        gold={"point.py": _t('"""Point helpers."""', "", "from dataclasses import dataclass", "", "@dataclass", "class Point:", "    x: int", "    y: int", "", "def make_point(x, y):", "    return Point(x, y)", "", "def distance(point):", "    return (point.x ** 2 + point.y ** 2) ** 0.5")},
    ))

    tasks.append(TaskSpec(
        id="refactor-resource-context",
        category="refactor",
        prompt="给 Resource 增加上下文管理器，确保 with 块结束时 closed=True；保留 open/close 方法。",
        files={
            "managed_resource.py": _t(
                '"""Resource lifecycle."""',
                "",
                "class Resource:",
                "    def __init__(self):",
                "        self.closed = False",
                "",
                "    def open(self):",
                "        return self",
                "",
                "    def close(self):",
                "        self.closed = True",
            ),
            "test_resource.py": _t(
                "from managed_resource import Resource",
                "",
                "def test_context_manager():",
                "    with Resource().open() as r:",
                "        assert r.closed is False",
                "    assert r.closed is True",
            ),
        },
        check="{python} -m pytest -q test_resource.py",
        gold={"managed_resource.py": _t('"""Resource lifecycle."""', "", "class Resource:", "    def __init__(self):", "        self.closed = False", "", "    def open(self):", "        return self", "", "    def close(self):", "        self.closed = True", "", "    def __enter__(self):", "        return self.open()", "", "    def __exit__(self, exc_type, exc, tb):", "        self.close()", "        return False")},
    ))

    tasks.append(TaskSpec(
        id="refactor-clock-injection",
        category="refactor",
        prompt="把 now() 改为可注入 clock：默认仍用 time.time，测试可传固定 clock。",
        files={
            "clock.py": _t(
                '"""Clock helper."""',
                "",
                "import time",
                "",
                "def now():",
                "    return time.time()",
            ),
            "test_clock.py": _t(
                "from clock import now",
                "",
                "def test_injected_clock():",
                "    assert now(lambda: 123.0) == 123.0",
                "",
                "def test_default_clock():",
                "    assert isinstance(now(), float)",
            ),
        },
        check="{python} -m pytest -q test_clock.py",
        gold={"clock.py": _t('"""Clock helper."""', "", "import time", "", "def now(clock=None):", "    return (clock or time.time)()")},
    ))

    tasks.append(TaskSpec(
        id="refactor-custom-error",
        category="refactor",
        prompt="为 require_positive() 定义 ValidationError；非法输入抛它而不是 ValueError，合法输入返回原值。",
        files={
            "validate.py": _t(
                '"""Validation helpers."""',
                "",
                "def require_positive(value):",
                "    if value <= 0:",
                "        raise ValueError('must be positive')",
                "    return value",
            ),
            "test_validate.py": _t(
                "import pytest",
                "from validate import require_positive, ValidationError",
                "",
                "def test_error_type():",
                "    with pytest.raises(ValidationError):",
                "        require_positive(0)",
                "",
                "def test_ok():",
                "    assert require_positive(2) == 2",
            ),
        },
        check="{python} -m pytest -q test_validate.py",
        gold={"validate.py": _t('"""Validation helpers."""', "", "class ValidationError(ValueError):", "    pass", "", "def require_positive(value):", "    if value <= 0:", "        raise ValidationError('must be positive')", "    return value")},
    ))

    # ------------------------------------------------------------------ test
    tasks.append(TaskSpec(
        id="test-parametrize-edges",
        category="test",
        prompt="为 divide() 补 4 个参数化用例：正常、除零、负数、浮点；运行后应为 6 passed。",
        files={
            "divide.py": _t(
                '"""Division helper."""',
                "",
                "def divide(a, b):",
                "    if b == 0:",
                "        raise ZeroDivisionError('b must not be zero')",
                "    return a / b",
            ),
            "test_divide.py": _t(
                "import pytest",
                "from divide import divide",
                "",
                "def test_normal():",
                "    assert divide(6, 3) == 2",
                "",
                "def test_zero():",
                "    with pytest.raises(ZeroDivisionError):",
                "        divide(1, 0)",
            ),
        },
        check="{python} -m pytest -q test_divide.py",
        gold={"test_divide.py": _t("import pytest", "from divide import divide", "", "def test_normal():", "    assert divide(6, 3) == 2", "", "def test_zero():", "    with pytest.raises(ZeroDivisionError):", "        divide(1, 0)", "", "@pytest.mark.parametrize('a,b,want', [(10, 2, 5), (-9, 3, -3), (1.0, 4.0, 0.25)])", "def test_parametrized(a, b, want):", "    assert divide(a, b) == want", "", "def test_negative_zero():", "    assert divide(0, -2) == 0")},
        check_contains=("6 passed",),
    ))

    tasks.append(TaskSpec(
        id="test-async-fetch",
        category="test",
        prompt="test_fetch.py 已有 1 个异步测试；请再增加 1 个 pytest.mark.asyncio 测试（断言返回 'ok' 或类型），运行后应为 2 passed。",
        files={
            "fetch.py": _t(
                '"""Async fetch stub."""',
                "",
                "async def fetch():",
                "    return 'ok'",
            ),
            "test_fetch.py": _t(
                "import pytest",
                "from fetch import fetch",
                "",
                "@pytest.mark.asyncio",
                "async def test_fetch():",
                "    assert await fetch() == 'ok'",
            ),
        },
        check="{python} -m pytest -q test_fetch.py",
        gold={"test_fetch.py": _t("import pytest", "from fetch import fetch", "", "@pytest.mark.asyncio", "async def test_fetch():", "    assert await fetch() == 'ok'", "", "@pytest.mark.asyncio", "async def test_fetch_type():", "    assert isinstance(await fetch(), str)")},
        check_contains=("2 passed",),
    ))

    tasks.append(TaskSpec(
        id="test-edge-coverage",
        category="test",
        prompt="为 clamp() 补 3 个边界用例（下界、上界、区间内），运行后应为 5 passed。",
        files={
            "clamp.py": _t(
                '"""Clamp helper."""',
                "",
                "def clamp(value, low, high):",
                "    if low > high:",
                "        raise ValueError('low > high')",
                "    return max(low, min(high, value))",
            ),
            "test_clamp.py": _t(
                "import pytest",
                "from clamp import clamp",
                "",
                "def test_middle():",
                "    assert clamp(5, 0, 10) == 5",
                "",
                "def test_bad_range():",
                "    with pytest.raises(ValueError):",
                "        clamp(5, 10, 0)",
            ),
        },
        check="{python} -m pytest -q test_clamp.py",
        gold={"test_clamp.py": _t("import pytest", "from clamp import clamp", "", "def test_middle():", "    assert clamp(5, 0, 10) == 5", "", "def test_bad_range():", "    with pytest.raises(ValueError):", "        clamp(5, 10, 0)", "", "def test_low():", "    assert clamp(-1, 0, 10) == 0", "", "def test_high():", "    assert clamp(11, 0, 10) == 10", "", "def test_boundary():", "    assert clamp(10, 0, 10) == 10")},
        check_contains=("5 passed",),
    ))

    # -------------------------------------------------------------- retrieval
    tasks.append(TaskSpec(
        id="retrieval-symbol",
        category="retrieval",
        prompt="在仓库里找到 parse_config() 的定义，把相对路径和行号写入新建的 ANSWER.md（大写文件名），格式 path:line。",
        files={
            "ANSWER.md": "TODO\n",
            "app/config.py": _t(
                '"""Config parser."""',
                "",
                "def parse_config(text):",
                "    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)",
            ),
            "app/main.py": "from app.config import parse_config\n",
        },
        check="{python} -c \"from pathlib import Path; t=Path('ANSWER.md').read_text(); assert 'app/config.py:' in t\"",
        gold={"ANSWER.md": "app/config.py:4\n"},
        tags=("search", "symbol"),
    ))

    tasks.append(TaskSpec(
        id="retrieval-crossfile",
        category="retrieval",
        prompt="app/main.py 导入 parse_config 失败，请找到正确模块并修复导入；运行 test_main.py。",
        files={
            "app/__init__.py": "",
            "app/config.py": _t(
                '"""Config parser."""',
                "",
                "def parse_config(text):",
                "    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)",
            ),
            "app/main.py": "from config import parse_config\n",
            "test_main.py": _t(
                "from app.main import parse_config",
                "",
                "def test_parse():",
                "    assert parse_config('a=1') == {'a': '1'}",
            ),
        },
        check="{python} -m pytest -q test_main.py",
        gold={"app/main.py": "from app.config import parse_config\n"},
        tags=("search", "import"),
    ))

    return tasks
