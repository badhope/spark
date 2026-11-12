"""Workdir sandbox with path-escape and command-injection guards."""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from spark.config import SparkConfig, default_home
from spark.errors import PathEscapeError
from spark.platform_compat import IS_WINDOWS
from spark.platform_compat import WINDOWS_RESERVED_NAMES as _WINDOWS_RESERVED


def safe_bare_filename(filename: str) -> str | None:
    """接受"工作目录内的裸文件名"，拒绝任何可能逃逸的路径形态。

    拒绝：空/带首尾空白/``.``/``..``/超长、含分隔符或盘符或 NUL、任何含
    ``..`` 的名字、Windows 设备名（con/prn/aux/nul）。合法时原样返回。
    """
    if not filename or filename != filename.strip():
        return None
    if filename in {".", ".."} or len(filename) > 255:
        return None
    if any(sep in filename for sep in ("/", "\\", ":", "\x00")):
        return None
    if ".." in filename:
        return None
    stem = filename.split(".")[0].lower()
    if stem in _WINDOWS_RESERVED:
        return None
    if filename.rstrip(" .") != filename:
        # Windows silently strips trailing dots/spaces, so "CON " or "x. " would
        # resolve to an unexpected name.
        return None
    return filename


# ``_logical()`` strips the drive letter, so these POSIX-shaped entries also
# match C:/Windows, D:/Program Files and friends on Windows.
DEFAULT_PROTECTED = [
    "/etc",
    "/root/.spark",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
    "/windows",
    "/program files",
    "/program files (x86)",
]

# Spark's own code and config: protected in every mode except unrestricted.

# Workdir-relative entries that tools must never write.  A git hook is
# persistent code execution: .git/hooks/post-commit survives every checkpoint
# rollback (snapshots skip .git wholesale) and runs on the user's next commit.
RELATIVE_PROTECTED = (
    ".git/hooks",
    ".git/config",
    ".git/info",
    ".gitmodules",
)


def self_protected() -> list[str]:
    """Paths Spark must never let a tool write to.

    These used to be hardcoded container paths (``/workspace/src/spark``) that
    matched nothing on a real machine, so "the agent protects its own code" was
    silently a no-op. Resolved per call so ``SPARK_HOME`` overrides apply.
    """
    import spark as _spark_pkg

    paths = [str(Path(_spark_pkg.__file__).resolve().parent)]
    config_toml = default_home() / "config.toml"
    paths.append(str(config_toml))
    return paths

# Commands that read sensitive material or modify system state; blocked outside full-access.
DENY_PATTERNS = (
    "sudo",
    "su ",
    "su\t",
    "shutdown",
    "reboot",
    "poweroff",
    "mkfs",
    "fdisk",
    "dd if=",
    "iptables",
    "passwd",
    "visudo",
    "systemctl enable",
    "systemctl disable",
    "curl http://169.254.169.254",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf $home",
    "/etc/shadow",
    # Git history rewrite / remote side effects. The system prompt promises the
    # agent never runs these, so the promise has to be enforced, not aspirational.
    "git push",
    "git pull",
    "git reset --hard",
    "git clean -f",
    "git checkout .",
    "git restore .",
    # Windows-native equivalents of the destructive POSIX commands above; the
    # list used to be POSIX-only, so it did not protect this platform at all.
    # "remove-item" is a bare verb: _VERB_SWITCHES requires a recursive switch
    # in any order, which covers `Remove-Item x -Recurse` and alias spellings
    # (`ri -r`, `del /s` handled separately below).
    "remove-item",
    "del /s",
    "rd /s",
    "rmdir /s",
    "format",
    "net user",
    "reg delete",
    "vssadmin delete",
)


class SandboxPolicyError(PermissionError):
    pass


# Patterns that must match as a *prefix* rather than a whole word: `rm -rf /home`
# is as destructive as `rm -rf /`, and the original substring check caught it.
_PREFIX_PATTERNS = frozenset(
    {"rm -rf /", "rm -rf ~", "rm -rf $home", "dd if=", "del /s", "rd /s", "rmdir /s"}
)

# Verbs whose destructive-ness depends on a switch appearing anywhere in the
# argv, in either order: `Remove-Item x -Recurse`, `ri -r x`.
_VERB_SWITCHES: dict[str, tuple[str, ...]] = {
    "remove-item": ("-recurse", "-r", "-rec", "-r:", "-force"),
}

# Destructive verbs that are only destructive in *command position* (the first
# word of a pipeline segment). Bare `format`/`su` anywhere would block
# `npm run format` and `echo su`.
_COMMAND_POSITION_PATTERNS = frozenset({"format", "su", "shutdown", "reboot", "poweroff", "passwd"})


def _command_position_hit(word: str, variant: str) -> bool:
    """True when *word* appears where a shell would treat it as a command."""
    pattern = rf"(?:^|[|;&]|&&|\|\|)\s*{re.escape(word)}(?![\w-])"
    return re.search(pattern, variant) is not None


def _deny_pattern_hits(needle: str, variants: tuple[str, ...]) -> bool:
    """Decide whether *needle* occurs as a command, not as text inside a word.

    Three shapes, because one rule cannot express all of them:

    * prefix patterns (`rm -rf /`) — plain containment, so deeper targets such as
      ``/home`` stay blocked (a word boundary would have let them through);
    * verb+switch patterns (`remove-item -recurse`) — verb present *and* any of
      its recursive switches, either order, so ``Remove-Item x -Recurse`` hits;
    * everything else (`su`, `git push`) — word-bounded, so `echo survived` and
      `npm run format` are not blocked.
    """
    for variant in variants:
        if needle in _PREFIX_PATTERNS:
            if needle in variant:
                return True
            continue
        words = needle.split()
        switches = _VERB_SWITCHES.get(needle) if len(words) == 1 else None
        if switches is not None:
            # Alias expansion turns `ri -r x` into `remove-item -r x`; the verb
            # alone is harmless (a path could contain "remove-item"), so require
            # the verb AND a recursive switch.
            if not re.search(rf"(?<![\w-]){re.escape(needle)}(?![\w-])", variant):
                continue
            for switch in switches:
                if re.search(rf"(?<![\w-]){re.escape(switch)}(?![\w-])", variant):
                    return True
            continue
        core = needle.rstrip()
        if core in _COMMAND_POSITION_PATTERNS:
            if _command_position_hit(core, variant):
                return True
            continue
        if re.search(rf"(?<![\w-]){re.escape(core)}(?![\w-])", variant):
            return True
    return False


def _normalize_redirections(command: str) -> str:
    """Put spaces around redirections so they act as token separators.

    Without this, `echo x>.git/hooks/pre-commit` keeps `x>` glued to the path,
    the path token never matches the protected list, and the file still gets
    written. It also stops `2>/dev/null` from being read as a /dev path.

    Note there is no digit lookbehind: `x>>file` must split too. A leading fd
    number (`2>`) is kept attached to the operator so it stays one token.
    """
    return re.sub(r"\s*(\d?&?>>?|\d?<)\s*", r" \1 ", command)


def _git_option_guard(tokens: list[str]) -> str | None:
    """Reject git invocations that can execute code or retarget the repo.

    ``git -c alias.x='!cmd' x`` is git's own shell escape; ``-C`` / ``--git-dir``
    / ``--work-tree`` let a command act on a different repository than the one
    the sandbox validated.
    """
    if not tokens or tokens[0].lower() not in {"git", "git.exe"}:
        return None
    for token in tokens[1:]:
        low = token.lower()
        if low.startswith("-calias.") or low.startswith("-c") and "alias." in low:
            return "command blocked: git alias definitions can execute arbitrary commands"
        if low in {"-c", "--config-env"} and "alias." in " ".join(tokens).lower():
            return "command blocked: git alias definitions can execute arbitrary commands"
        if low.startswith("--git-dir") or low.startswith("--work-tree"):
            return "command blocked: git --git-dir/--work-tree would leave the workdir"
    return None


def _git_control_surface(path: Path, root: Path) -> str | None:
    """Return the git control entry *path* falls under, at any depth.

    Checks path *components* rather than prefixes so a nested repository
    (`vendor/x/.git/hooks/pre-commit`), a submodule (`.git/modules/m/hooks`) and
    any depth of the same shape are all covered.
    """
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        rel = path
    parts = [p.lower() for p in rel.parts]
    for i, part in enumerate(parts[:-1]):
        if part == ".git":
            following = parts[i + 1]
            if following in {"hooks", "config", "info", "objects", "refs", "modules"}:
                return ".git/" + following
    if parts and parts[-1] == ".gitmodules":
        return ".gitmodules"
    if parts and parts[-1] == ".gitattributes":
        return ".gitattributes"
    return None


def _expand_env_indirection(command: str) -> str:
    """Add a variant of *command* with ``$VAR`` / ``%VAR%`` expanded.

    `%SPARK_HOME%\\config.toml` contains no literal protected prefix, yet the
    shell expands it into one — and overwriting Spark's config means persistent
    code execution (it holds hooks and MCP commands). Only variables whose value
    is an existing path are expanded; everything else is left alone.
    """
    expanded = command
    lowered = command.lower()
    for name, value in os.environ.items():
        if not value or len(value) < 3:
            continue
        # Command text may have been lower-cased earlier; try both spellings so
        # `%SPARK_HOME%` still expands after case folding.
        for form in (f"%{name}%", f"%{name.lower()}%", f"${{{name}}}", f"${{{name.lower()}}}", f"${name}", f"${name.lower()}"):
            if form in expanded:
                expanded = expanded.replace(form, value)
            elif form in lowered:
                # Preserve the original case of the rest of the command.
                expanded = re.sub(re.escape(form), value, expanded, flags=re.IGNORECASE)
    return expanded


def _strip_quotes(token: str) -> str:
    """Drop quote characters from a shell token.

    Two problems on Windows: ``shlex`` in non-POSIX mode leaves quotes in place,
    and cmd.exe *removes mid-token quotes before executing* — so
    ``"C:\\Win"dows\\win.ini`` really opens ``C:\\Windows\\win.ini`` while the
    policy only ever saw ``C:\\Win``. Removing every quote character (not just a
    surrounding pair) makes the checked string match what the shell will open.
    """
    return token.replace('"', "").replace("'", "").strip()


# PowerShell ships aliases that map onto the destructive POSIX names. The deny
# list is written with POSIX spellings, so expand the aliases before matching —
# `ri -recurse` and `Remove-Item -Recurse` must hit the same rule.
_PS_ALIASES: tuple[tuple[str, str], ...] = (
    (r"\bri\b", "remove-item"),
    (r"\bdel\b", "remove-item"),
    (r"\berase\b", "remove-item"),
    (r"\brmdir\b", "remove-item"),
    (r"\brd\b", "remove-item"),
    (r"\bcp\b", "copy-item"),
    (r"\bmv\b", "move-item"),
)


def _powershell_alias_expand(command: str) -> str:
    expanded = command
    for pattern, replacement in _PS_ALIASES:
        expanded = re.sub(pattern, replacement, expanded)
    return expanded


# Absolute-looking path fragments: POSIX (/a/b) and Windows (C:\a\b, C:/a/b,
# \\server\share). Quoted and space-containing forms are covered by the quote
# characters the pattern accepts around each fragment.
_PATH_FRAGMENT_RE = re.compile(
    r"""(?:
        (?P<win>[A-Za-z]:[\\/][^"'`\s;|&<>()]*)
      | (?P<unc>\\\\[^"'`\s;|&<>()]+)
      | (?P<posix>/(?:[^"'`\s;|&<>()]+))
    )""",
    re.VERBOSE,
)


_ALLOWED_DEVICE_SINKS = frozenset(
    {"/dev/null", "/dev/zero", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty"}
)


def _is_device_sink(path: Path | str) -> bool:
    """True for the harmless standard device files (null/zero/std*/tty).

    Compared on the *logical* form: on Windows ``Path("/dev/null")`` resolves
    against the current drive (``C:/dev/null``, ``D:/dev/null``, ...), so a
    drive-specific prefix check was wrong. Strip any drive or extended-length
    prefix before comparing the POSIX device path.
    """
    text = str(path).replace("\\", "/").lower()
    if text.startswith("//?/"):
        text = text[4:]
    elif text.startswith("//./"):
        text = text[4:]
    if len(text) >= 3 and text[1] == ":" and text[2] == "/":
        text = text[2:]
    text = text.rstrip("/")
    return text in _ALLOWED_DEVICE_SINKS


def _path_candidates(command: str) -> list[str]:
    """Every absolute-path-looking fragment in *command*, quotes included.

    Used as a second, text-level pass so a path that never surfaces as a clean
    token (inside ``"$(...)"``, ``${IFS}`` splits, or backticks) is still
    checked against the protected list.
    """
    out: list[str] = []
    for match in _PATH_FRAGMENT_RE.finditer(command):
        fragment = match.group(0).strip().strip("\"'`")
        if not fragment:
            continue
        # Trim a trailing operator the regex may have swallowed.
        fragment = fragment.rstrip(",;")
        if fragment and fragment not in out:
            out.append(fragment)
    return out


class WorkdirSandbox:
    def __init__(
        self,
        workdir: Path,
        cfg: SparkConfig | None = None,
        extra_roots: list[Path] | None = None,
    ) -> None:
        self.root = workdir.resolve()
        self.cfg = cfg
        self.sandbox_mode = getattr(cfg.agent, "sandbox_mode", "workspace") if cfg else "workspace"
        self.protected_paths = list(getattr(cfg.agent, "protected_paths", []) or []) if cfg else []
        #: 额外可访问根目录（--add-dir / agent.extra_dirs）。
        #: 语义刻意保守：只放行这些目录内的文件读写，不放宽 shell 越界，
        #: 也不放宽保护路径。加目录是为了让 agent 能读同级的兄弟仓库，
        #: 不是为了给它一台新机器。
        self.extra_roots: list[Path] = []
        # 配置里的 extra_dirs 是默认来源，显式参数在其后追加/去重。
        configured = list(getattr(cfg.agent, "extra_dirs", []) or []) if cfg else []
        for raw in [*configured, *(extra_roots or [])]:
            try:
                resolved = Path(raw).expanduser().resolve()
            except OSError:
                continue
            if resolved != self.root and resolved not in self.extra_roots:
                self.extra_roots.append(resolved)
        #: Tool name of the call currently being dispatched, set by the registry.
        #: Only used to attribute an audit entry; enforcement never reads it.
        self.current_tool: str = ""

    @property
    def roots(self) -> list[Path]:
        """所有允许的文件系统根（主工作目录 + 额外目录）。"""
        return [self.root, *self.extra_roots]

    def _containing_root(self, path: Path) -> Path | None:
        """返回 *path* 所属的允许根，或 None。"""
        for root in self.roots:
            try:
                path.relative_to(root)
                return root
            except ValueError:
                continue
        return None

    @property
    def shell_allowed(self) -> bool:
        return self.sandbox_mode in {"workspace", "full-access", "unrestricted"}

    @property
    def write_allowed(self) -> bool:
        return self.sandbox_mode in {"workspace", "full-access", "unrestricted"}

    @property
    def full_access(self) -> bool:
        return self.sandbox_mode in {"full-access", "unrestricted"}

    @property
    def unrestricted(self) -> bool:
        return self.sandbox_mode == "unrestricted"

    def resolve(self, rel: str) -> Path:
        raw = rel.strip() or "."
        # 绝对路径先按原样解析：`--add-dir` 的目录通常在工作目录之外，
        # 拼到 root 后面会得到一个无意义的名字。
        candidate = Path(raw)
        if candidate.is_absolute():
            path = candidate.expanduser().resolve()
        else:
            path = (self.root / raw).resolve()
        containing = self._containing_root(path)
        if containing is not None:
            self._check_protected(path)
            return path
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            if self.full_access:
                self._check_protected(path)
                return path
            from spark import audit

            audit.record(
                "sandbox_blocked",
                path=str(raw),
                matched="workdir_escape",
                root=str(self.root),
                tool=getattr(self, "current_tool", ""),
            )
            raise PathEscapeError(f"Path escapes workdir: {rel}") from exc
        self._check_protected(path)
        return path

    def _protected_effective(self) -> list[str]:
        if self.unrestricted:
            return self.protected_paths
        return self.protected_paths + DEFAULT_PROTECTED + self_protected() + list(
            RELATIVE_PROTECTED
        )

    @staticmethod
    def _logical(path: Path) -> Path:
        # Drive-stripped form so POSIX-style absolute rules still match on Windows.
        if path.drive:
            return Path("/") / path.relative_to(path.anchor)
        return path

    def _check_protected(self, path: Path) -> None:
        # Any .git control surface at any depth: a nested repo (`vendor/x/.git`)
        # or a submodule (`.git/modules/m/hooks`) is just as good a place to plant
        # a hook that runs on the user's next commit. 每个允许根都要查——
        # 只相对主 root 计算会让 --add-dir 目录里的 .git 完全逃过检查。
        hit = None
        for root in self.roots:
            hit = _git_control_surface(path, root)
            if hit:
                break
        if hit:
            from spark import audit

            audit.record(
                "sandbox_blocked",
                path=str(path),
                matched=str(hit),
                root=str(getattr(self, "root", "")),
                tool=getattr(self, "current_tool", ""),
            )
            raise SandboxPolicyError(f"Blocked protected path: {hit}")
        for raw in self._protected_effective():
            p = Path(raw)
            base = p if (p.is_absolute() or raw[:1] in ("/", "\\")) else (self.root / p)
            try:
                self._logical(path).relative_to(self._logical(base))
            except ValueError:
                continue
            raise SandboxPolicyError(f"Blocked protected path: {raw}")

    def is_path_allowed(self, path: Path) -> bool:
        """True 当 *path* 未命中保护路径清单（供只读遍历工具过滤候选文件）。"""
        try:
            self._check_protected(path)
        except SandboxPolicyError:
            return False
        return True

    def _command_tokens(self, command: str) -> list[str]:
        """Sanitise a shell command into safe tokens.

        Replaces shell metacharacters, command substitutions, and chaining
        operators with spaces before tokenising. Best-effort — not a security
        boundary; the real boundary is ``check_shell`` which decides allow/deny.
        """
        cleaned = command
        # IFS must be neutralised *before* the generic "${" / "$" rules below,
        # otherwise "${IFS}" collapses to "IFS}" and the leftover token no longer
        # looks like a variable at all. ${IFS} is the standard way to smuggle a
        # space or a separator past token-based checks (`cat${IFS}/etc/passwd`).
        cleaned = cleaned.replace("${IFS}", " ").replace("$IFS", " ")
        # Block-process substitution: <(cmd) and >(cmd)
        for pat in ("<(", ">(", "$(", "${", "$["):
            cleaned = cleaned.replace(pat, " ")
        # Command chaining and redirection
        for ch in ("|", ";", "&&", "||", "\n", "\r", "\t"):
            cleaned = cleaned.replace(ch, " ")
        # Backticks and any remaining bare $
        cleaned = cleaned.replace("`", " ").replace("$", " ")
        try:
            tokens = shlex.split(cleaned, posix=os.name != "nt")
        except ValueError:
            tokens = cleaned.split()
        # On Windows shlex runs in non-POSIX mode, which keeps the quote
        # characters inside the token ("C:\..." stays quoted), so a protected
        # path written with quotes used to slip past the containment check.
        return [_strip_quotes(tok) for tok in tokens if _strip_quotes(tok)]

    def _command_hits_protected(
        self, command: str, paths: list[str] | None = None, cwd: Path | None = None
    ) -> str | None:
        """Return the protected path entry hit by a path token in the command, if any.

        ``paths`` 缺省为生效清单（含内置凭据保护）；unrestricted 档传入用户清单。

        Two passes, because tokenising alone is easy to hide from:

        * token pass — normal commands and quoted paths (the common case);
        * raw pass — escapes any absolute path that appears anywhere in the
          command text, including inside quotes and command substitutions such
          as ``cat "$(echo /etc/passwd)"``. That form never yields a clean path
          token, yet the shell still opens the file.

        Relative candidates are resolved against *both* the workdir root and the
        command's *cwd*: ``run_shell(cwd="a")`` with ``../.git/hooks/x`` must be
        judged the same as the same path from the root, and env-indirection
        (``%SPARK_HOME%/config.toml``) is expanded before matching.
        """
        effective = paths if paths is not None else self._protected_effective()
        bases = [
            (raw, (Path(raw) if Path(raw).is_absolute() else (self.root / raw)).resolve())
            for raw in effective
        ]

        def hit_by(candidate: Path) -> str | None:
            # /dev/null, /dev/zero and /dev/std* are streams, not files: writing
            # there discloses nothing and `2>/dev/null` appears in most build
            # commands. They are exempt from the /dev protection rule.
            if _is_device_sink(candidate):
                return None
            for raw, base in bases:
                try:
                    self._logical(candidate).relative_to(self._logical(base))
                    return raw
                except ValueError:
                    continue
            return None

        bases_for_relative = [self.root]
        if cwd is not None and cwd != self.root:
            bases_for_relative.append(cwd)

        def resolve_any(text: str) -> list[Path]:
            out: list[Path] = []
            try:
                raw_path = Path(text)
            except (OSError, ValueError):
                return out
            if raw_path.is_absolute():
                try:
                    out.append(raw_path.resolve())
                except OSError:
                    pass
                return out
            for base in bases_for_relative:
                try:
                    out.append((base / raw_path).resolve())
                except OSError:
                    continue
            return out

        expanded = _expand_env_indirection(command)
        for source in {command, expanded}:
            for token in self._command_tokens(source):
                for cand in resolve_any(_strip_quotes(token)):
                    hit = hit_by(cand)
                    if hit:
                        return hit

        for source in {command, expanded}:
            for candidate in _path_candidates(source):
                for path in resolve_any(_strip_quotes(candidate)):
                    hit = hit_by(path)
                    if hit:
                        return hit
        return None

    def check_shell(self, command: str, cwd: Path | None = None) -> str | None:
        """Return a rejection reason, or None when the command may run.

        Deny patterns are matched against a *normalised* command so the same
        intent is caught on both platforms: case folding (Windows/cmd is
        case-insensitive), collapsed whitespace (``rm  -rf  /``), and the
        PowerShell verb aliases that map onto the destructive POSIX names.

        *cwd* must be the directory the process will actually run in, otherwise
        relative paths (`../.git/hooks/x`) are judged against the wrong base.
        """
        if self.unrestricted:
            hit_user = self._command_hits_protected(
                command, self.protected_paths, cwd=cwd
            )
            if hit_user:
                return f"command blocked: references protected path {hit_user}"
            return None
        if self.full_access:
            hit = self._command_hits_protected(command, cwd=cwd)
            if hit:
                return f"command blocked: references protected path {hit}"
            return None
        normalised = " ".join(command.strip().lower().split())
        # Split redirections like `>`/`>>` into their own tokens so a protected
        # relative target next to a redirection (`echo x>.git/hooks/pre-commit`)
        # cannot hide, and so `2>/dev/null` does not look like a /dev path.
        normalised = _normalize_redirections(normalised)
        variants = (normalised, _powershell_alias_expand(normalised))
        for pattern in DENY_PATTERNS:
            needle = pattern.strip().lower()
            if not needle:
                continue
            if _deny_pattern_hits(needle, variants):
                return f"command blocked by sandbox policy (pattern: {needle})"
        try:
            tokens = shlex.split(normalised, posix=not IS_WINDOWS)
        except ValueError:
            tokens = normalised.split()
        tokens = [_strip_quotes(tok) for tok in tokens]
        # git's own flags can re-enable arbitrary execution or move the repo:
        # `git -c alias.x='!cmd' x`, `git -C <dir> push`, `--work-tree`.
        git_reason = _git_option_guard(tokens)
        if git_reason:
            return git_reason
        for token in tokens:
            # `env` / `printenv` dump the parent environment, which can carry API
            # keys. Match on the bare command word only, not on any path segment.
            if token.lower() in {"env", "printenv"}:
                return f"command blocked: reading environment via '{token}' is not allowed"
        # Credential paths: match both separators (Windows accepts either and
        # `.ssh\config` used to slip through the slash-only list).
        credential_markers = (
            "/proc/self/environ",
            "id_rsa",
            "id_ed25519",
            ".ssh/",
            ".ssh\\",
            ".aws/",
            ".aws\\",
            ".netrc",
            ".npmrc",
            ".git-credentials",
        )
        probe = _expand_env_indirection(normalised)
        if any(marker in probe for marker in credential_markers):
            return "command blocked: access to credential paths is not allowed"
        # Use the normalised form: redirections are separated there, so
        # `x>.git/hooks/...` yields a real path token instead of a glued one.
        hit = self._command_hits_protected(normalised, cwd=cwd)
        if hit:
            return f"command blocked: references protected path {hit}"
        return None

    def check_write_path(self, path: Path) -> str | None:
        if self.unrestricted:
            try:
                self._check_protected(path)
            except SandboxPolicyError as exc:
                return str(exc)
            return None
        try:
            path.relative_to(self.root)
        except ValueError:
            if self.full_access:
                try:
                    self._check_protected(path)
                except SandboxPolicyError as exc:
                    return str(exc)
                return None
            return f"write blocked outside workdir: {path}"
        try:
            self._check_protected(path)
        except SandboxPolicyError as exc:
            return str(exc)
        return None
