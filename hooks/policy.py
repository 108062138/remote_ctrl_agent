"""Remote Safety Policy：判断 Claude 的一次工具调用是 Allow、Ask 还是 Deny。

  Allow  自动放行（只读、在工作目录里）
  Ask    手机上跳出允许 / 拒绝（可逆的修改）
  Deny   直接拒绝，手机批准也没用（不可逆、范围大、碰到机密）

这里只有判断逻辑（纯函数），不读 stdin、不输出，方便测试。
接到 Claude Code 的部分在 remote_guard.py。

阅读顺序：
  1. Decision / Context           判断结果、判断时需要的路径资讯
  2. classify_path()              一个路径属于：机密 / 受保护 / 工作目录内 / 暂存 / 外面 / 不确定
  3. decide()                     入口：依工具种类分派
  4. analyze_bash()               Bash 指令：用 bashlex 拆开后逐段判断
  5. judge_argv() 和 _judge_*     单一指令的规则（git、rm、curl……）

限制（写在这里，避免误以为万无一失）：
  - 执行脚本（python x.py、npm test、make）时，脚本内容无法分析，所以一律 Ask，由你在手机上看过再批准。
  - 这层是「远端模式的护栏」，不是沙箱。真正不可逆的操作请回电脑前做。
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import bashlex

# --- 1. 判断结果与路径资讯 ---------------------------------------------------------

ALLOW, ASK, DENY = 0, 1, 2
LEVEL_NAME = {ALLOW: "allow", ASK: "ask", DENY: "deny"}


@dataclass(frozen=True)
class Decision:
    level: int
    reason: str

    @property
    def name(self) -> str:
        return LEVEL_NAME[self.level]


def worst(decisions, default: Decision | None = None) -> Decision:
    """多个判断取最严格的（Deny > Ask > Allow）。"""
    result = default or Decision(ALLOW, "只读操作")
    for d in decisions:
        if d.level > result.level:
            result = d
    return result


SENSITIVE_HOME_PATHS = (
    ".ssh", ".gnupg", ".aws", ".azure", ".config/gcloud", ".kube", ".docker",
    ".config/gh", ".password-store", ".local/share/keyrings", "Library/Keychains",
    ".claude/.credentials.json", ".claude.json", ".netrc", ".git-credentials",
    ".npmrc", ".pypirc",
)
SENSITIVE_NAME_RE = re.compile(
    r"^(\.env(\..+)?|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|.+\.(pem|p12|pfx|keychain|keychain-db)"
    r"|\.netrc|\.git-credentials|\.npmrc|\.pypirc)$"
)
SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}
DEVICE_SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}

SENSITIVE, PROTECTED, INSIDE, SCRATCH, OUTSIDE, UNKNOWN = (
    "sensitive", "protected", "inside", "scratch", "outside", "unknown")


def _real(p: Path | str) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


@dataclass
class Context:
    """判断时需要的路径。全部是 realpath（已解开 symlink）。"""
    work_dir: Path
    home: Path
    tool_root: Path
    config_dir: Path
    cwd: Path | None = None  # None = 不确定（例如 cd 到变数路径之后）
    sensitive_dirs: tuple = field(default=())
    protected: tuple = field(default=())
    scratch: tuple = field(default=())


def make_context(work_dir, *, cwd=None, home=None, tool_root=None, config_dir=None) -> Context:
    home = _real(home or Path.home())
    work_dir = _real(work_dir)
    tool_root = _real(tool_root or Path(__file__).resolve().parent.parent)
    config_dir = _real(config_dir or home / ".config" / "remote-ctrl")
    sensitive = tuple(_real(home / p) for p in SENSITIVE_HOME_PATHS)
    protected = tuple(_real(p) for p in (
        # 安全层本身：不能被远端修改或关掉
        tool_root / "hooks", tool_root / "deploy", tool_root / "scripts",
        tool_root / "preflight", tool_root / "platform_utils.py", config_dir,
        home / ".claude" / "settings.json", home / ".claude" / "settings.local.json",
        home / ".claude" / "hooks",
        # 工作目录里能让程序「之后自动执行」的设定
        work_dir / ".claude", work_dir / ".mcp.json",
        work_dir / ".git" / "hooks", work_dir / ".git" / "config",
    ))
    scratch = tuple(sorted({_real(p) for p in ("/tmp", "/var/tmp", os.environ.get("TMPDIR") or "/tmp")}))
    return Context(work_dir=work_dir, home=home, tool_root=tool_root, config_dir=config_dir,
                   cwd=_real(cwd) if cwd else work_dir,
                   sensitive_dirs=sensitive, protected=protected, scratch=scratch)


# --- 2. 路径分类 ---------------------------------------------------------------------

def _inside(p: Path, base: Path) -> bool:
    return p == base or base in p.parents


def _sensitive_name(name: str) -> bool:
    return name not in SAFE_ENV_NAMES and bool(SENSITIVE_NAME_RE.match(name))


def resolve_word(word: str, ctx: Context) -> Path | None:
    """把指令里的一个字解析成绝对路径；含变数等无法确定时回传 None。"""
    w = word
    for var in ("${HOME}", "$HOME"):
        if w.startswith(var):
            w = str(ctx.home) + w[len(var):]
    if "$" in w or "`" in w:
        return None
    if w == "~" or w.startswith("~/"):
        w = str(ctx.home) + w[1:]
    elif w.startswith("~"):
        return None  # ~other_user
    for i, ch in enumerate(w):  # 万用字元：只看前面确定的资料夹
        if ch in "*?[":
            head = w[:i]
            w = head.rsplit("/", 1)[0] if "/" in head else ""
            break
    p = Path(w) if w else Path(".")
    if not p.is_absolute():
        if ctx.cwd is None:
            return None
        p = ctx.cwd / p
    return _real(p)


GLOB_CHARS = "*?["


def _glob_may_hit_sensitive(word: str, ctx: Context) -> bool:
    """万用字元（.env*、~/.ss*/id_*）：实际展开一次，并防「.e*」这种可能对到 .env 的写法。"""
    name = word.rstrip("/").rsplit("/", 1)[-1]
    prefix = name[: min((name.find(c) for c in GLOB_CHARS if c in name), default=len(name))]
    if prefix.startswith(".") and (".env".startswith(prefix) or prefix.startswith(".env")):
        return True
    w = word
    for var in ("${HOME}", "$HOME"):
        if w.startswith(var):
            w = str(ctx.home) + w[len(var):]
    if w == "~" or w.startswith("~/"):
        w = str(ctx.home) + w[1:]
    if "$" in w or "`" in w or (not os.path.isabs(w) and ctx.cwd is None):
        return False
    pattern = w if os.path.isabs(w) else str(ctx.cwd / w)
    for i, match in enumerate(glob.iglob(pattern, recursive=True)):
        if i > 1000:
            break
        p = _real(match)
        if _sensitive_name(p.name) or any(_inside(p, s) for s in ctx.sensitive_dirs):
            return True
    return False


def classify_path(word: str, ctx: Context) -> str:
    if _sensitive_name(word.rstrip("/").rsplit("/", 1)[-1]):
        return SENSITIVE
    if any(c in word for c in GLOB_CHARS) and _glob_may_hit_sensitive(word, ctx):
        return SENSITIVE
    if word in DEVICE_SINKS:
        return SCRATCH
    p = resolve_word(word, ctx)
    if p is None:
        return UNKNOWN
    if _sensitive_name(p.name) or any(_inside(p, s) for s in ctx.sensitive_dirs):
        return SENSITIVE
    if any(_inside(p, s) for s in ctx.protected):
        return PROTECTED
    if _inside(p, ctx.work_dir):
        return INSIDE
    if any(_inside(p, s) for s in ctx.scratch):
        return SCRATCH
    return OUTSIDE


def _contains_guarded(word: str, ctx: Context) -> bool:
    """这个路径是不是「包含」机密或受保护的东西（例如 mv 整个上层资料夹）。"""
    p = resolve_word(word, ctx)
    if p is None:
        return False
    return any(_inside(g, p) for g in ctx.protected + ctx.sensitive_dirs)


# --- 3. 入口：依工具分派 --------------------------------------------------------------

READ_TOOLS = {"Read": "file_path", "NotebookRead": "notebook_path", "LS": "path",
              "Glob": "path", "Grep": "path"}
WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
HARMLESS_TOOLS = {"TodoWrite", "TodoRead", "ExitPlanMode", "AskUserQuestion", "ToolSearch"}


def decide(tool_name: str, tool_input: dict, ctx: Context) -> Decision:
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return Decision(DENY, "Bash 没有指令内容")
        return analyze_bash(command, ctx)
    if tool_name in READ_TOOLS:
        return _judge_read_tool(tool_name, tool_input, ctx)
    if tool_name in WRITE_TOOLS:
        return _judge_write_tool(tool_name, tool_input, ctx)
    if tool_name in HARMLESS_TOOLS:
        return Decision(ALLOW, "不会动到档案的内部工具")
    if tool_name in ("WebFetch", "WebSearch"):
        return Decision(ASK, "连到外部网站")
    return Decision(ASK, f"未分类的工具 {tool_name}，请确认")


def _judge_read_tool(tool_name, tool_input, ctx) -> Decision:
    words = []
    path = tool_input.get(READ_TOOLS[tool_name])
    if isinstance(path, str) and path:
        words.append(path)
    pattern = tool_input.get("pattern")
    if tool_name == "Glob" and isinstance(pattern, str) and pattern:
        words.append(pattern if not path else os.path.join(path, pattern))
    return worst(_path_rule(w, ctx, "read") for w in words or ["."])


def _judge_write_tool(tool_name, tool_input, ctx) -> Decision:
    path = tool_input.get(WRITE_TOOLS[tool_name])
    if not isinstance(path, str) or not path:
        return Decision(DENY, "看不到要写入的档案路径")
    return _path_rule(path, ctx, "write")


def _path_rule(word: str, ctx: Context, mode: str, destructive: bool = False) -> Decision:
    """mode: read（只读）/ write（会写或删）/ other（执行别的程序）。"""
    cls = classify_path(word, ctx)
    if cls == SENSITIVE:
        return Decision(DENY, f"碰到机密档案：{word}")
    if mode == "read":
        if cls in (OUTSIDE, UNKNOWN):
            return Decision(ASK, f"读取工作目录外的路径：{word}")
        return Decision(ALLOW, "只读")
    if cls == PROTECTED:
        return Decision(DENY, f"不能修改安全层或自动执行设定：{word}")
    if mode == "write":
        if cls in (OUTSIDE, UNKNOWN):
            return Decision(DENY, f"不能写入 / 删除工作目录外或无法确定的路径：{word}")
        if destructive and _contains_guarded(word, ctx):
            return Decision(DENY, f"这个路径包含受保护的档案：{word}")
        return Decision(ASK, f"会修改档案：{word}")
    return Decision(ASK, "执行程式")


def _path_args(args):
    """挑出可能是路径的参数：略过选项，--opt=value 取 value，略过网址。"""
    for a in args:
        if a.startswith("-"):
            if a.startswith("--") and "=" in a:
                a = a.split("=", 1)[1]
            else:
                continue
        if "://" in a or a == "":
            continue
        yield a


def _check_paths(args, ctx, mode, destructive=False) -> Decision:
    base = Decision(ALLOW, "只读") if mode == "read" else Decision(ASK, "会修改档案" if mode == "write" else "执行程式")
    return worst((_path_rule(a, ctx, mode, destructive) for a in _path_args(args)), base)


# --- 4. Bash：拆开组合指令 ------------------------------------------------------------

MAX_DEPTH = 3


def analyze_bash(command: str, ctx: Context, depth: int = 0) -> Decision:
    if depth > MAX_DEPTH:
        return Decision(DENY, "指令层层包装太深，无法确认")
    if not command.strip():
        return Decision(ALLOW, "空指令")
    try:
        trees = bashlex.parse(command)
    except Exception:  # bashlex 会丢各种错误
        return Decision(DENY, "看不懂这条指令（远端模式不执行无法分析的指令），请改写成简单的形式")
    a = _Analyzer(ctx, depth)
    try:
        for t in trees:
            a.visit(t)
    except Exception as e:  # 未知语法：保守拒绝
        return Decision(DENY, f"无法分析这条指令：{type(e).__name__}")
    return worst(a.decisions)


class _Analyzer:
    """走过 bashlex 的语法树，每遇到一个指令就判断一次。会追踪 cd 后的目前目录。"""

    def __init__(self, ctx: Context, depth: int):
        self.ctx = replace(ctx)
        self.depth = depth
        self.decisions: list[Decision] = []

    def visit(self, node, pipe_pos: int = 0):
        kind = node.kind
        if kind == "list":
            for p in node.parts:
                if p.kind == "operator":
                    if p.op == "&":
                        self.decisions.append(Decision(DENY, "不能在背景执行（&）"))
                else:
                    self.visit(p)
        elif kind == "pipeline":
            i = 0
            for p in node.parts:
                if p.kind == "pipe":
                    continue
                self.visit(p, pipe_pos=i)
                i += 1
        elif kind == "command":
            self.command(node, pipe_pos)
        elif kind == "function":
            self.decisions.append(Decision(DENY, "不能定义函数（例如 fork bomb）"))
        elif kind == "compound":
            for p in node.list:
                self.visit(p)
            for r in getattr(node, "redirects", []) or []:
                self.visit(r)
        elif kind in ("if", "for", "while", "until"):
            for p in node.parts:
                self.visit(p)
        elif kind == "word":
            self.substitutions(node)
        elif kind == "redirect":
            self.redirect(node)
        elif kind in ("reservedword", "operator", "pipe", "parameter", "tilde", "heredoc"):
            pass
        else:
            self.decisions.append(Decision(DENY, f"不支持的语法：{kind}"))

    def substitutions(self, node):
        """$(...)、<(...) 里面的指令也要判断。"""
        for part in getattr(node, "parts", []) or []:
            if part.kind in ("commandsubstitution", "processsubstitution"):
                self.visit(part.command)
            elif part.kind in ("word", "assignment"):
                self.substitutions(part)

    def command(self, node, pipe_pos):
        words = []
        for p in node.parts:
            if p.kind == "assignment":
                self.substitutions(p)
            elif p.kind == "word":
                self.substitutions(p)
                words.append(p.word)
            elif p.kind == "redirect":
                self.redirect(p)
            else:
                self.visit(p)
        if not words:
            return
        self.decisions.append(judge_argv(words, self.ctx, piped=pipe_pos > 0, depth=self.depth))
        self.track_cd(words)

    def track_cd(self, words):
        name = os.path.basename(words[0])
        if name in ("pushd", "popd"):
            self.ctx.cwd = None
        elif name == "cd":
            target = words[1] if len(words) > 1 else "~"
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
        if mode == "write" and target in DEVICE_SINKS:
            return
        self.decisions.append(_path_rule(target, self.ctx, mode))


# --- 5. 单一指令的规则 ----------------------------------------------------------------

DENY_NAMES = {
    # 提权
    "sudo": "提权", "su": "提权", "doas": "提权", "pkexec": "提权",
    # 系统
    "shutdown": "关机", "reboot": "重启", "halt": "关机", "poweroff": "关机",
    "systemctl": "管理系统服务", "launchctl": "管理系统服务", "service": "管理系统服务",
    "mount": "挂载磁碟", "umount": "卸载磁碟", "iptables": "改防火墙", "ufw": "改防火墙",
    "pfctl": "改防火墙", "csrutil": "改系统安全设定", "spctl": "改系统安全设定",
    "passwd": "改密码", "useradd": "改使用者", "userdel": "改使用者", "usermod": "改使用者",
    "visudo": "改 sudo 设定", "crontab": "设定排程", "at": "设定排程",
    # 磁碟
    "dd": "直接写磁碟", "fdisk": "分割磁碟", "sfdisk": "分割磁碟", "parted": "分割磁碟",
    "diskutil": "磁碟工具", "wipefs": "清除磁碟", "mkswap": "格式化", "shred": "永久抹除档案",
    # 权限 / 拥有者
    "chown": "改拥有者", "chgrp": "改群组",
    # 网络外传
    "scp": "传档到远端", "sftp": "传档到远端", "ftp": "传档到远端", "ssh": "连到其他机器",
    "nc": "网络连线工具", "ncat": "网络连线工具", "netcat": "网络连线工具", "socat": "网络连线工具",
    "telnet": "网络连线工具",
    # 进程 / 常驻
    "killall": "批次结束程式", "pkill": "批次结束程式", "nohup": "常驻背景程式",
    "setsid": "常驻背景程式", "disown": "常驻背景程式", "screen": "常驻背景程式", "tmux": "常驻背景程式",
    # 无法分析
    "eval": "无法分析的动态指令", "source": "执行无法分析的脚本", ".": "执行无法分析的脚本",
    # 机密
    "security": "读取钥匙圈", "secret-tool": "读取钥匙圈", "osascript": "控制 macOS 应用程式",
    # 部署 / 云端
    "vercel": "部署", "netlify": "部署", "firebase": "部署", "fly": "部署", "flyctl": "部署",
    "heroku": "部署", "kubectl": "操作 Kubernetes", "helm": "操作 Kubernetes", "terraform": "改云端资源",
    "pulumi": "改云端资源", "aws": "操作云端账号", "gcloud": "操作云端账号", "az": "操作云端账号",
    "doctl": "操作云端账号", "wrangler": "部署", "serverless": "部署", "sls": "部署",
    "ansible-playbook": "改远端机器",
}

NO_PATH_CMDS = {"echo", "printf", "pwd", "date", "whoami", "id", "uname", "hostname", "true",
                "false", "sleep", "seq", "which", "type", ":", "uptime", "nproc", "arch", "tty",
                "basename", "dirname", "expr"}
READ_ONLY_CMDS = {"ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "ag", "ack",
                  "tree", "file", "stat", "du", "df", "diff", "cmp", "comm", "sort", "uniq", "cut",
                  "tr", "nl", "column", "jq", "less", "more", "realpath", "readlink", "md5sum",
                  "sha1sum", "sha256sum", "shasum", "md5", "cksum", "hexdump", "xxd", "od",
                  "strings", "paste", "fold", "fmt", "rev", "tac", "expand", "unexpand", "test",
                  "[", "ps", "free", "bat"}
WRITE_CMDS = {"rmdir", "mv", "cp", "ln", "touch", "mkdir", "tee", "truncate", "install", "patch",
              "unlink", "split", "csplit"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
INTERPRETERS = {"python", "perl", "ruby", "node", "php", "deno", "bun", "lua", "rscript"}
DB_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "mongo", "mongosh", "redis-cli"}
DB_DANGER_RE = re.compile(r"\b(drop|truncate|flushall|flushdb)\b", re.I)

# 前缀包装：去掉后看真正执行的指令
WRAPPERS = {"env", "nice", "time", "command", "builtin", "exec", "stdbuf", "timeout", "xargs"}
UNKNOWN_ARG = "$UNKNOWN"


def _strip_wrappers(words: list[str]) -> list[str]:
    while words:
        name = os.path.basename(words[0])
        if name not in WRAPPERS:
            return words
        rest = words[1:]
        if name == "env":
            while rest and (rest[0].startswith("-") or "=" in rest[0]):
                rest = rest[1:]
            if not rest:
                return ["env"]  # 单独 env = 印出环境变量
        elif name == "nice":
            if rest[:1] == ["-n"]:
                rest = rest[2:]
            while rest and rest[0].startswith("-"):
                rest = rest[1:]
        elif name == "timeout":
            while rest and rest[0].startswith("-"):
                rest = rest[1:]
            rest = rest[1:]  # 时间长度
        elif name == "xargs":
            takes_value = {"-I", "-n", "-P", "-L", "-d", "-s", "-a", "-E", "--max-args", "--max-procs"}
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in takes_value else rest[1:]
            rest = (rest or ["echo"]) + [UNKNOWN_ARG]  # 参数来自前一个指令，无法确定
        else:
            while rest and rest[0].startswith("-"):
                rest = rest[1:]
        words = rest
    return words


def judge_argv(words: list[str], ctx: Context, piped: bool = False, depth: int = 0) -> Decision:
    words = _strip_wrappers(list(words))
    if not words:
        return Decision(ALLOW, "空指令")
    raw_name, args = words[0], words[1:]
    if "$" in raw_name or "`" in raw_name:
        return Decision(DENY, "指令名称是变数，无法确认要执行什么")
    name = os.path.basename(raw_name).lower()

    if name in DENY_NAMES:
        return Decision(DENY, f"远端模式禁止：{name}（{DENY_NAMES[name]}）")
    if name.startswith("mkfs"):
        return Decision(DENY, "远端模式禁止：格式化磁碟")

    # 任何指令只要碰到机密档案就拒绝
    for a in _path_args(args):
        if classify_path(a, ctx) == SENSITIVE:
            return Decision(DENY, f"碰到机密档案：{a}")

    special = _SPECIAL.get(name)
    if special:
        return special(args, ctx, piped, depth)
    if name in SHELLS:
        return _judge_shell(args, ctx, piped, depth)
    if name.startswith("python") or name in INTERPRETERS:
        return _judge_interpreter(name, args, ctx, piped)
    if name in DB_CLIENTS:
        if any(DB_DANGER_RE.search(a) for a in args):
            return Decision(DENY, "远端模式禁止删除资料库内容（DROP / TRUNCATE / FLUSH）")
        return Decision(ASK, "操作资料库")
    if name in ("npm", "yarn", "pnpm", "bun") and ({"publish", "unpublish"} & set(args)):
        return Decision(DENY, "远端模式禁止发布套件")
    if (name, args[:1]) in (("twine", ["upload"]), ("cargo", ["publish"]), ("gem", ["push"]),
                            ("docker", ["push"]), ("docker", ["login"])):
        return Decision(DENY, "远端模式禁止发布 / 上传")
    if name in NO_PATH_CMDS:
        return Decision(ALLOW, "只读")
    if name in READ_ONLY_CMDS:
        if name == "sort" and ("-o" in args or any(a.startswith("--output") for a in args)):
            return _check_paths(args, ctx, "write")
        return _check_paths(args, ctx, "read")
    if name in WRITE_CMDS:
        return _check_paths(args, ctx, "write", destructive=name in ("mv", "rmdir", "unlink"))
    if name in ("env", "printenv"):
        return Decision(ASK, "会印出环境变量（可能含机密）")
    return _check_paths(args, ctx, "other")


def _judge_cd(args, ctx, piped, depth):
    return Decision(ALLOW, "切换目录")


def _judge_rm(args, ctx, piped, depth):
    for a in args:
        if a in ("--recursive", "--no-preserve-root") or (
                a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a)):
            return Decision(DENY, "远端模式禁止递归删除（rm -r / rm -rf）")
    if not list(_path_args(args)):
        return Decision(ASK, "删除档案")
    return _check_paths(args, ctx, "write", destructive=True)


def _judge_chmod(args, ctx, piped, depth):
    if any(a in ("-R", "--recursive") or (a.startswith("-") and "R" in a) for a in args):
        return Decision(DENY, "远端模式禁止递归改权限（chmod -R）")
    return _check_paths(args, ctx, "write", destructive=True)


def _judge_find(args, ctx, piped, depth):
    decisions = []
    starts = []
    i = 0
    while i < len(args) and not args[i].startswith(("-", "(", "!")):
        starts.append(args[i])
        i += 1
    decisions.append(_check_paths(starts or ["."], ctx, "read"))
    while i < len(args):
        a = args[i]
        if a == "-delete":
            return Decision(DENY, "远端模式禁止 find -delete")
        if a in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            sub = []
            while j < len(args) and args[j] not in (";", "+"):
                sub.append(UNKNOWN_ARG if "{}" in args[j] else args[j])
                j += 1
            decisions.append(judge_argv(sub, ctx, depth=depth))
            i = j
        elif a.startswith("-fprint") or a == "-fls":
            decisions.append(_path_rule(args[i + 1] if i + 1 < len(args) else UNKNOWN_ARG, ctx, "write"))
        i += 1
    return worst(decisions)


GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
GIT_RISKY_CONFIG = ("hookspath", "fsmonitor", "sshcommand", "alias.", "credential", "editor",
                    "pager", "external", "textconv", "filter.", "gitproxy", "askpass")
GIT_READ = {"status", "diff", "log", "show", "blame", "ls-files", "rev-parse", "describe",
            "shortlog", "grep", "reflog", "ls-tree", "cat-file", "whatchanged", "--version", "help"}


def _judge_git(args, ctx, piped, depth):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i]
        if opt == "-c" and i + 1 < len(args):
            if any(k in args[i + 1].lower() for k in GIT_RISKY_CONFIG):
                return Decision(DENY, f"远端模式禁止用 -c 改会执行程式的 git 设定：{args[i + 1]}")
        if opt in ("--version", "--help"):
            return Decision(ALLOW, "只读")
        i += 2 if opt in GIT_GLOBAL_WITH_VALUE else 1
    if i >= len(args):
        return Decision(ALLOW, "只读")
    sub, rest = args[i], args[i + 1:]
    flags = set(rest)

    def deny(why):
        return Decision(DENY, f"远端模式禁止 git {sub}：{why}")

    if sub == "push":
        return deny("会推送到远端，无法从电脑这边收回")
    if sub in ("filter-branch", "filter-repo"):
        return deny("改写整个历史")
    if sub == "reset" and "--hard" in flags:
        return deny("会丢掉未 commit 的修改")
    if sub == "clean" and any(f in ("--force",) or (f.startswith("-") and not f.startswith("--") and "f" in f) for f in rest):
        return deny("会删除未追踪的档案")
    if sub == "checkout" and ("--" in flags or "." in flags or "-f" in flags or "--force" in flags):
        return deny("会丢掉档案的修改")
    if sub == "restore" and not ({"--staged", "-S"} & flags and not {"--worktree", "-W"} & flags):
        return deny("会丢掉档案的修改")
    if sub == "branch" and {"-d", "-D", "--delete", "-m", "-M", "--move", "-f", "--force"} & flags:
        return deny("删除或改名分支")
    if sub == "remote" and rest[:1] and rest[0] in ("add", "remove", "rm", "set-url", "rename", "prune"):
        return deny("修改远端设定")
    if sub == "stash" and rest[:1] and rest[0] in ("drop", "clear"):
        return deny("会丢掉暂存的修改")
    if sub == "reflog" and rest[:1] and rest[0] in ("expire", "delete"):
        return deny("会删掉还原用的记录")
    if sub == "gc" and any(f.startswith("--prune") for f in rest):
        return deny("会删掉还原用的物件")
    if sub == "update-ref" and "-d" in flags:
        return deny("删除参照")
    if sub == "config":
        if {"--get", "--list", "-l", "--get-regexp", "--get-all"} & flags:
            return Decision(ALLOW, "只读")
        return deny("修改 git 设定可能让程式之后自动执行")
    if sub == "submodule" and rest[:1] == ["deinit"]:
        return deny("移除子模组")
    if sub == "worktree" and rest[:1] == ["remove"] and ("--force" in flags or "-f" in flags):
        return deny("强制移除工作树")
    if sub in GIT_READ:
        return Decision(ALLOW, "只读")
    if sub == "branch" and (not rest or flags <= {"-a", "-r", "--list", "-v", "-vv", "--all"}):
        return Decision(ALLOW, "只读")
    if sub == "remote" and (not rest or rest[0] in ("-v", "show", "get-url")):
        return Decision(ALLOW, "只读")
    if sub == "stash" and rest[:1] and rest[0] in ("list", "show"):
        return Decision(ALLOW, "只读")
    if sub == "tag" and (not rest or flags <= {"-l", "--list"}):
        return Decision(ALLOW, "只读")
    return Decision(ASK, f"git {sub} 会修改 repo")


CURL_SEND_FLAGS = {"-T", "--upload-file", "-d", "--data", "--data-binary", "--data-raw",
                   "--data-urlencode", "--data-ascii", "-F", "--form", "--form-string", "--json"}


def _judge_curl(args, ctx, piped, depth):
    for i, a in enumerate(args):
        key = a.split("=", 1)[0]
        if key in CURL_SEND_FLAGS:
            return Decision(DENY, "远端模式禁止用 curl 送资料到外部")
        if a.startswith("-") and not a.startswith("--") and set(a[1:]) & {"T", "d", "F"}:
            return Decision(DENY, "远端模式禁止用 curl 送资料到外部")
        if key in ("-X", "--request"):
            method = a.split("=", 1)[1] if "=" in a else (args[i + 1] if i + 1 < len(args) else "")
            if method.upper() not in ("GET", "HEAD"):
                return Decision(DENY, f"远端模式禁止 curl {method} 请求")
        if key in ("-o", "--output") and i + 1 < len(args) and "=" not in a:
            d = _path_rule(args[i + 1], ctx, "write")
            if d.level == DENY:
                return d
    return Decision(ASK, "从网络下载")


def _judge_wget(args, ctx, piped, depth):
    for i, a in enumerate(args):
        key = a.split("=", 1)[0]
        if key in ("--post-data", "--post-file", "--method", "--body-data", "--body-file"):
            return Decision(DENY, "远端模式禁止用 wget 送资料到外部")
        if key in ("-O", "--output-document") and i + 1 < len(args) and args[i + 1] != "-":
            d = _path_rule(args[i + 1], ctx, "write")
            if d.level == DENY:
                return d
    return Decision(ASK, "从网络下载")


def _judge_rsync(args, ctx, piped, depth):
    for a in args:
        if a.startswith("--delete"):
            return Decision(DENY, "远端模式禁止 rsync --delete")
        if not a.startswith("-") and ("::" in a or "rsync://" in a or re.match(r"^[^/]+:", a)):
            return Decision(DENY, "远端模式禁止 rsync 到其他机器")
    return _check_paths(args, ctx, "write")


def _judge_kill(args, ctx, piped, depth):
    if {"-9", "-KILL", "-SIGKILL"} & set(args) or any(
            a == "-s" and i + 1 < len(args) and args[i + 1].upper() in ("KILL", "SIGKILL", "9")
            for i, a in enumerate(args)):
        return Decision(DENY, "远端模式禁止 kill -9")
    return Decision(ASK, "结束程式")


def _judge_sed(args, ctx, piped, depth):
    if any(a == "-i" or a.startswith("-i") or a.startswith("--in-place") for a in args):
        return _check_paths(args, ctx, "write")
    return _check_paths(args, ctx, "read")


GH_READ = {("pr", "view"), ("pr", "list"), ("pr", "diff"), ("pr", "status"), ("pr", "checks"),
           ("issue", "view"), ("issue", "list"), ("repo", "view"), ("run", "list"),
           ("run", "view"), ("status", None)}


def _judge_gh(args, ctx, piped, depth):
    key = (args[0] if args else None, args[1] if len(args) > 1 else None)
    if key in GH_READ or (key[0], None) in GH_READ:
        return Decision(ALLOW, "只读")
    return Decision(DENY, "远端模式禁止用 gh 修改 GitHub 上的东西")


def _judge_shell(args, ctx, piped, depth):
    if "-c" in args:
        i = args.index("-c")
        if i + 1 >= len(args):
            return Decision(DENY, "看不到 -c 要执行的内容")
        return analyze_bash(args[i + 1], ctx, depth + 1)
    if piped or not [a for a in args if not a.startswith("-")]:
        return Decision(DENY, "远端模式禁止把前一个指令的输出直接当程式执行（例如 curl ... | sh）")
    return _check_paths(args, ctx, "other")


def _judge_interpreter(name, args, ctx, piped):
    if piped and (not args or args[0] == "-"):
        return Decision(DENY, f"远端模式禁止把前一个指令的输出直接交给 {name} 执行")
    return _check_paths(args, ctx, "other")


_SPECIAL = {
    "cd": _judge_cd, "rm": _judge_rm, "chmod": _judge_chmod, "find": _judge_find,
    "git": _judge_git, "curl": _judge_curl, "wget": _judge_wget, "rsync": _judge_rsync,
    "kill": _judge_kill, "sed": _judge_sed, "gh": _judge_gh,
}
