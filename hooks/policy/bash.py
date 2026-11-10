"""Bash 指令：用 bashlex 拆开后，每个指令交给 judge_argv() 判断，取最严格的结果。

处理的事：
  - 组合指令（&& ; || |）、$(...)、<(...)、回圈、if、subshell、{ ...; }
  - 背景执行（&）、定义函数 → Deny；看不懂的语法 → Deny
  - VAR=x cmd、export VAR=x：改 PATH、PAGER、LD_PRELOAD 这类会让程式执行别的东西的变数 → Deny
  - 去掉包装：env、nice、time、timeout、stdbuf、xargs、command、builtin、exec……
  - 追踪 cd（含 cd -P、builtin cd），subshell 和管线里的 cd 不影响外面
  - 管线后面接 sh / python 读 stdin（curl ... | sh）→ Deny
"""

from __future__ import annotations

import os
import re
from dataclasses import replace

import bashlex

from . import commands
from .core import ALLOW, ASK, DENY, Context, Decision, Unclear, worst
from .options import leading, spec
from .paths import PROTECTED, SENSITIVE, classify_path, path_args, path_rule, resolve_word

MAX_DEPTH = 3
UNKNOWN_ARG = "$UNKNOWN"   # 参数来自别处（xargs、find -exec {}），无法确定

DANGEROUS_ENV_RE = re.compile(
    r"^(LD_\w+|DYLD_\w+|PAGER|GIT_PAGER|MANPAGER|SYSTEMD_PAGER|EDITOR|VISUAL|GIT_EDITOR|"
    r"GIT_SSH|GIT_SSH_COMMAND|GIT_EXEC_PATH|GIT_CONFIG\w*|GIT_DIR|GIT_WORK_TREE|GIT_ASKPASS|"
    r"SSH_ASKPASS|SUDO_ASKPASS|BASH_ENV|ENV|PROMPT_COMMAND|PS[0-4]|PYTHONSTARTUP|PYTHONPATH|"
    r"PYTHONHOME|NODE_OPTIONS|NODE_PATH|PERL5OPT|PERL5LIB|RUBYOPT|RUBYLIB|LESSOPEN|LESSCLOSE|"
    r"PATH|IFS|SHELLOPTS|BASHOPTS|CDPATH|HOME|TMPDIR|XDG_CONFIG_HOME|ANTHROPIC_\w+|CLAUDE_\w+|"
    r"REMOTE_CTRL_\w+|GIT_INDEX_FILE|GIT_OBJECT_DIRECTORY|GIT_ALTERNATE_OBJECT_DIRECTORIES)$"
)
ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\+?=")
COMMAND_NAME_RE = re.compile(r"^[a-z][a-z0-9._+-]*$")  # 像指令名称的字（排除 .、路径、样式）


def assignment_rule(word: str) -> Decision | None:
    m = ASSIGN_RE.match(word)
    if m and DANGEROUS_ENV_RE.match(m.group(1)):
        return Decision(DENY, f"远端模式禁止设定 {m.group(1)}（可能让别的程式被执行或绕过检查）")
    return None


# --- 入口 --------------------------------------------------------------------------

def analyze_bash(command: str, ctx: Context, depth: int = 0, strict: bool = True) -> Decision | None:
    """strict=False 时，看不懂就回传 None（用在「参数里藏指令」的辅助检查）。"""
    if depth > MAX_DEPTH:
        return Decision(DENY, "指令层层包装太深，无法确认")
    if not command.strip():
        return Decision(ALLOW, "空指令")
    try:
        trees = bashlex.parse(command)
    except Exception:  # bashlex 会丢各种错误
        if not strict:
            return None
        return Decision(DENY, "看不懂这条指令（远端模式不执行无法分析的指令），请改写成简单的形式")
    a = _Analyzer(ctx, depth)
    try:
        for t in trees:
            a.visit(t)
    except Exception as e:
        if not strict:
            return None
        return Decision(DENY, f"无法分析这条指令：{type(e).__name__}")
    return worst(a.decisions)


class _Analyzer:
    def __init__(self, ctx: Context, depth: int):
        self.ctx = replace(ctx)
        self.depth = depth
        self.decisions: list[Decision] = []

    def visit(self, node, piped: bool = False):
        kind = node.kind
        if kind == "list":
            for p in node.parts:
                if p.kind == "operator":
                    if p.op == "&":
                        self.decisions.append(Decision(DENY, "不能在背景执行（&）"))
                else:
                    self.visit(p, piped)
        elif kind == "pipeline":
            segments = [p for p in node.parts if p.kind != "pipe"]
            for i, p in enumerate(segments):
                saved = self.ctx.cwd
                self.visit(p, piped or i > 0)
                if len(segments) > 1:
                    self.ctx.cwd = saved  # 管线每一段都在 subshell
        elif kind == "command":
            self.command(node, piped)
        elif kind == "function":
            self.decisions.append(Decision(DENY, "不能定义函数（例如 fork bomb）"))
        elif kind == "compound":
            subshell = bool(node.list) and getattr(node.list[0], "word", None) == "("
            saved = self.ctx.cwd
            for p in node.list:
                self.visit(p, piped)
            if subshell:
                self.ctx.cwd = saved
            for r in getattr(node, "redirects", []) or []:
                self.visit(r)
        elif kind in ("if", "for", "while", "until"):
            for p in node.parts:
                self.visit(p, piped)
        elif kind == "word":
            self.substitutions(node)
        elif kind == "redirect":
            self.redirect(node)
        elif kind in ("reservedword", "operator", "pipe", "parameter", "tilde", "heredoc"):
            pass
        else:
            self.decisions.append(Decision(DENY, f"不支持的语法：{kind}"))

    def substitutions(self, node):
        for part in getattr(node, "parts", []) or []:
            if part.kind in ("commandsubstitution", "processsubstitution"):
                saved = self.ctx.cwd
                self.visit(part.command)
                self.ctx.cwd = saved
            elif part.kind in ("word", "assignment"):
                self.substitutions(part)

    def command(self, node, piped):
        words = []
        for p in node.parts:
            if p.kind == "assignment":
                self.substitutions(p)
                d = assignment_rule(p.word)
                if d:
                    self.decisions.append(d)
            elif p.kind == "word":
                self.substitutions(p)
                words.append(p.word)
            elif p.kind == "redirect":
                self.redirect(p)
            else:
                self.visit(p)
        if not words:
            return
        self.decisions.append(judge_argv(words, self.ctx, piped=piped, depth=self.depth))
        self.track_cd(words)

    def track_cd(self, words):
        try:
            words, _ = strip_wrappers(list(words), self.ctx, self.depth)
        except Unclear:
            self.ctx.cwd = None
            return
        if not words:
            return
        name = os.path.basename(words[0])
        if name in ("pushd", "popd"):
            self.ctx.cwd = None
        elif name == "cd":
            try:
                _, rest = leading(words[1:], spec(letters="LPe@"))
            except Unclear:
                self.ctx.cwd = None
                return
            target = rest[0] if rest else "~"
            self.ctx.cwd = None if target == "-" else resolve_word(target, self.ctx)

    def redirect(self, node):
        out = getattr(node, "output", None)
        if out is None or isinstance(out, int) or not hasattr(out, "word"):
            return  # 2>&1 这类
        self.substitutions(out)
        rtype, target = node.type, out.word
        if rtype in ("<<", "<<-", "<<<"):
            return  # heredoc 只是文字资料
        if rtype.endswith("&") and (target.isdigit() or target == "-"):
            return
        mode = "read" if rtype in ("<", "<&") else "write"
        self.decisions.append(path_rule(target, self.ctx, mode))


# --- 去掉包装指令 ----------------------------------------------------------------------

WRAPPERS = {
    "env": spec(letters="i0v", short_val="uCS", longs={"--ignore-environment", "--null", "--debug"},
                long_val={"--unset", "--chdir", "--split-string"}),
    "nice": spec(short_val="n", long_val={"--adjustment"}, numeric=True),
    "time": spec(letters="pvaq", short_val="fo", long_val={"--format", "--output"},
                 longs={"--portability", "--verbose", "--append", "--quiet"}),
    "command": spec(letters="pvV"),
    "builtin": spec(),
    "exec": spec(letters="cl", short_val="a"),
    "stdbuf": spec(short_val="ioe", long_val={"--input", "--output", "--error"}),
    "timeout": spec(letters="v", short_val="sk", long_val={"--signal", "--kill-after"},
                    longs={"--preserve-status", "--foreground", "--verbose"}),
    "xargs": spec(letters="0rtpx", short_val="IndPLasE",
                  long_val={"--max-args", "--max-procs", "--max-lines", "--delimiter", "--arg-file",
                            "--max-chars", "--eof", "--process-slot-var"},
                  long_optval={"--replace"},
                  longs={"--null", "--no-run-if-empty", "--verbose", "--interactive", "--exit", "--open-tty"}),
    "caffeinate": spec(letters="dimsu", short_val="tw"),
    "systemd-inhibit": spec(long_val={"--what", "--who", "--why", "--mode"}),
    "ionice": spec(letters="t", short_val="cnpPu"),
    "unbuffer": spec(letters="p"),
    "chronic": spec(letters="ev"),
}


def strip_wrappers(words: list[str], ctx: Context, depth: int) -> tuple[list[str], list[Decision]]:
    """回传 (真正执行的指令, 包装本身产生的判断)。包装的选项看不懂时丢 Unclear。"""
    extra: list[Decision] = []
    while words:
        name = os.path.basename(words[0])
        s = WRAPPERS.get(name)
        if s is None:
            return words, extra
        opts, rest = leading(words[1:], s)
        names = {o for o, _ in opts}
        if name == "env":
            if names & {"-C", "--chdir", "-S", "--split-string"}:
                raise Unclear("env -C / -S 无法分析")
            while rest and ASSIGN_RE.match(rest[0]):
                d = assignment_rule(rest[0])
                if d:
                    extra.append(d)
                rest = rest[1:]
            if not rest:
                return ["env"], extra  # 单独 env = 印出环境变量
        elif name == "command" and names & {"-v", "-V"}:
            return [], extra + [Decision(ALLOW, "查询指令位置")]
        elif name == "time":
            for o, v in opts:
                if o in ("-o", "--output"):
                    extra.append(path_rule(v, ctx, "write"))
        elif name == "timeout":
            if not rest:
                raise Unclear("timeout 缺少时间")
            rest = rest[1:]
        elif name == "xargs":
            rest = (rest or ["echo"]) + [UNKNOWN_ARG]
        words = rest
    return words, extra


# --- 单一指令 --------------------------------------------------------------------------

def judge_argv(words: list[str], ctx: Context, piped: bool = False, depth: int = 0) -> Decision:
    try:
        words, extra = strip_wrappers(list(words), ctx, depth)
    except Unclear as e:
        return Decision(DENY, f"看不懂包装指令的选项：{e}")
    if not words:
        return worst(extra, Decision(ALLOW, "只读"))
    return worst(extra + [_judge(words, ctx, piped, depth)], Decision(ALLOW, "只读"))


def _judge(words: list[str], ctx: Context, piped: bool, depth: int) -> Decision:
    raw_name, args = words[0], words[1:]
    if "$" in raw_name or "`" in raw_name:
        return Decision(DENY, "指令名称是变数，无法确认要执行什么")
    name = os.path.basename(raw_name).lower()

    # 直接执行安全层自己的程式（scripts/start-remote.sh 等）：可能开新 session 提高等级、停掉服务
    if "/" in raw_name and classify_path(raw_name, ctx) in (PROTECTED, SENSITIVE):
        return Decision(DENY, f"远端模式禁止执行安全层或受保护位置的程式：{raw_name}")

    denied = commands.deny_by_name(name, args)
    if denied:
        return denied

    # 任何指令只要碰到机密档案就拒绝
    for a in path_args(args):
        if classify_path(a, ctx) == SENSITIVE:
            return Decision(DENY, f"碰到机密档案：{a}")

    return commands.judge(name, args, ctx, piped, depth)


def embedded_danger(args: list[str], ctx: Context, depth: int) -> Decision | None:
    """参数里藏指令：docker run x sh -c '...'、watch 'git push'、caffeinate rm -rf x。"""
    if depth >= MAX_DEPTH:
        return None
    for k, a in enumerate(args):
        if a.startswith("-"):
            continue
        name = os.path.basename(a).lower()
        if COMMAND_NAME_RE.match(name) and commands.is_known_command(name):
            d = judge_argv(args[k:], ctx, depth=depth + 1)
            if d.level == DENY:
                return d
        if any(c.isspace() for c in a):
            d = analyze_bash(a, ctx, depth + 1, strict=False)
            if d is not None and d.level == DENY:
                return d
    return None
