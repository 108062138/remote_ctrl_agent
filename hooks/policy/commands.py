"""各指令的规则。judge() 是入口，依指令名称分派。

分派顺序：
  1. deny_by_name   一定拒绝的指令（sudo、ssh、dd、发布、部署……）
  2. SPECIAL        有专门规则的指令（git、rm、curl、wget、sed、find、cd……）
  3. shell / 直译器 sh -c '...' 会拆进去看；curl | sh、curl | python 读 stdin → Deny
  4. NO_PATH        不碰档案的指令（echo、pwd……）→ Allow
  5. READ_SPECS     只读白名单：指令 + 每个选项都认得才 Allow；不认得 → Ask
  6. WRITE_CMDS     会写档的指令 → 工作目录内 Ask，外面 Deny
  7. 其他           执行程式 → Ask（并检查参数里有没有藏危险指令）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import bash
from .core import ALLOW, ASK, DENY, Context, Decision, Unclear, worst
from .options import OptSpec, leading, scan, spec
from .paths import (INSIDE, OUTSIDE, UNKNOWN, check_paths, classify_path, glob_may_name_secret,
                    path_args, path_rule, recursive_read_rule)

# --- 1. 一定拒绝 ----------------------------------------------------------------------

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
    # 拥有者
    "chown": "改拥有者", "chgrp": "改群组",
    # 网络外传
    "scp": "传档到远端", "sftp": "传档到远端", "ftp": "传档到远端", "ssh": "连到其他机器",
    "nc": "网络连线工具", "ncat": "网络连线工具", "netcat": "网络连线工具", "socat": "网络连线工具",
    "telnet": "网络连线工具",
    # 进程 / 常驻
    "killall": "批次结束程式", "pkill": "批次结束程式", "nohup": "常驻背景程式",
    "setsid": "常驻背景程式", "disown": "常驻背景程式", "screen": "常驻背景程式", "tmux": "常驻背景程式",
    # 无法分析 / 改变之后的指令
    "eval": "无法分析的动态指令", "source": "执行无法分析的脚本", ".": "执行无法分析的脚本",
    "alias": "定义别名", "trap": "设定之后会自动执行的指令", "enable": "载入 shell 扩充",
    # 机密 / 系统控制
    "security": "读取钥匙圈", "secret-tool": "读取钥匙圈", "osascript": "控制 macOS 应用程式",
    # 部署 / 云端
    "vercel": "部署", "netlify": "部署", "firebase": "部署", "fly": "部署", "flyctl": "部署",
    "heroku": "部署", "kubectl": "操作 Kubernetes", "helm": "操作 Kubernetes", "terraform": "改云端资源",
    "pulumi": "改云端资源", "aws": "操作云端账号", "gcloud": "操作云端账号", "az": "操作云端账号",
    "doctl": "操作云端账号", "wrangler": "部署", "serverless": "部署", "sls": "部署",
    "ansible-playbook": "改远端机器",
}


def deny_by_name(name: str, args: list[str]) -> Decision | None:
    if name in DENY_NAMES:
        return Decision(DENY, f"远端模式禁止：{name}（{DENY_NAMES[name]}）")
    if name.startswith("mkfs"):
        return Decision(DENY, "远端模式禁止：格式化磁碟")
    if name in ("npm", "yarn", "pnpm", "bun") and {"publish", "unpublish"} & set(args):
        return Decision(DENY, "远端模式禁止发布套件")
    first = args[:1]
    if (name, first) in (("twine", ["upload"]), ("cargo", ["publish"]), ("gem", ["push"]),
                         ("docker", ["push"]), ("docker", ["login"]), ("poetry", ["publish"])):
        return Decision(DENY, "远端模式禁止发布 / 上传")
    if name == "hash" and "-p" in args:
        return Decision(DENY, "远端模式禁止 hash -p（把指令名称指到别的程式）")
    return None


# --- 2. 入口 ------------------------------------------------------------------------

SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
INTERPRETERS = {"python", "perl", "ruby", "node", "php", "deno", "bun", "lua", "rscript"}
DB_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "mongo", "mongosh", "redis-cli"}
DB_DANGER_RE = re.compile(r"\b(drop|truncate|flushall|flushdb)\b", re.I)
NO_PATH_CMDS = {"echo", "printf", "pwd", "whoami", "id", "true", "false", "sleep", "seq", "which",
                "type", ":", "uptime", "nproc", "arch", "tty", "basename", "dirname", "expr", "uname"}
WRITE_CMDS = {"rmdir", "mv", "cp", "ln", "touch", "mkdir", "tee", "truncate", "install", "patch",
              "unlink", "split", "csplit"}
DESTRUCTIVE = {"mv", "rmdir", "unlink"}
DECLARE_CMDS = {"export", "declare", "typeset", "readonly", "local"}


def is_known_command(name: str) -> bool:
    return (name in DENY_NAMES or name in SPECIAL or name in SHELLS or name in WRITE_CMDS
            or name.startswith("python") or name in INTERPRETERS)


def judge(name: str, args: list[str], ctx: Context, piped: bool, depth: int) -> Decision:
    if name in SPECIAL:
        return SPECIAL[name](args, ctx, piped, depth)
    if name in SHELLS:
        return _judge_shell(args, ctx, piped, depth)
    if name.startswith("python") or name in INTERPRETERS:
        return _judge_interpreter(name, args, ctx, piped, depth)
    if name in DB_CLIENTS:
        if any(DB_DANGER_RE.search(a) for a in args):
            return Decision(DENY, "远端模式禁止删除资料库内容（DROP / TRUNCATE / FLUSH）")
        return Decision(ASK, "操作资料库")
    if name in DECLARE_CMDS:
        for a in args:
            d = bash.assignment_rule(a)
            if d:
                return d
        return Decision(ALLOW, "设定变数")
    if name == "date":
        if any(a in ("-s", "--set") or a.startswith("--set=") for a in args):
            return Decision(ASK, "修改系统时间")
        return Decision(ALLOW, "只读")
    if name == "hostname":
        return Decision(ALLOW, "只读") if all(a.startswith("-") for a in args) else Decision(ASK, "修改主机名称")
    if name in NO_PATH_CMDS:
        return Decision(ALLOW, "只读")
    if name in READ_SPECS:
        return _judge_read(name, args, ctx)
    if name in WRITE_CMDS:
        return check_paths(args, ctx, "write", destructive=name in DESTRUCTIVE)
    if name in ("env", "printenv", "set"):
        return Decision(ASK, "会印出环境变量（可能含机密）")
    hidden = bash.embedded_danger(args, ctx, depth)
    if hidden:
        return hidden
    return check_paths(args, ctx, "other")


# --- 5. 只读白名单 ---------------------------------------------------------------------

@dataclass(frozen=True)
class ReadSpec:
    opts: OptSpec
    pattern_first: bool = False            # 第一个参数是搜寻样式，不是路径
    pattern_opts: frozenset = frozenset()  # 给了这些选项时，就没有「样式参数」
    read_opts: frozenset = frozenset()     # 选项的值是要读的档案
    write_opts: frozenset = frozenset()    # 选项的值是要写的档案
    glob_opts: frozenset = frozenset()     # 选项的值是档名样式（rg -g）
    max_inputs: int | None = None          # 超过的参数是输出档（uniq IN OUT、xxd IN OUT）
    recursive_opts: frozenset = frozenset()
    always_recursive: bool = False         # rg：预设就递回
    skips_hidden: bool = False             # rg：预设略过隐藏档
    hidden_opts: frozenset = frozenset()
    no_paths: bool = False                 # 参数都不是路径（tr）
    content: bool = True                   # 会读档案内容（tree、du、ls 只读档名）


def R(letters="", short_val="", longs=(), long_val=(), long_optval=(), numeric=False, any_flags=False, **kw):
    return ReadSpec(spec(letters, short_val, longs, long_val, long_optval, numeric, any_flags),
                    **{k: frozenset(v) if isinstance(v, (set, tuple, list)) else v for k, v in kw.items()})


_SUMS = R(letters="bctwz", short_val="a",
          longs={"--binary", "--check", "--text", "--tag", "--quiet", "--status", "--strict", "--warn",
                 "--zero", "--ignore-missing"})
_GREP = R(letters="rRnilvwcHhoqsEFGPxIZzUabTLy", short_val="efmABCdD",
          longs={"--recursive", "--dereference-recursive", "--line-number", "--ignore-case",
                 "--invert-match", "--word-regexp", "--count", "--with-filename", "--no-filename",
                 "--only-matching", "--quiet", "--silent", "--no-messages", "--extended-regexp",
                 "--fixed-strings", "--perl-regexp", "--line-regexp", "--files-with-matches",
                 "--files-without-match", "--null", "--text", "--basic-regexp"},
          long_val={"--regexp", "--file", "--max-count", "--after-context", "--before-context",
                    "--context", "--include", "--exclude", "--exclude-dir", "--exclude-from", "--label",
                    "--binary-files", "--directories", "--devices"},
          long_optval={"--color", "--colour"},
          pattern_first=True, pattern_opts={"-e", "--regexp", "-f", "--file"},
          read_opts={"-f", "--file", "--exclude-from"}, glob_opts={"--include"},
          recursive_opts={"-r", "-R", "--recursive", "--dereference-recursive"})

READ_SPECS: dict[str, ReadSpec] = {
    "ls": R(letters="aAlhRtrSd1FiGnogcuUvxkpQbBLHsm",
            longs={"--all", "--almost-all", "--human-readable", "--recursive", "--reverse", "--directory",
                   "--classify", "--inode", "--size", "--group-directories-first", "--full-time",
                   "--no-group", "--numeric-uid-gid", "--literal", "--dereference"},
            long_val={"--sort", "--time-style", "--time", "--format", "--indicator-style", "--ignore",
                      "--hide", "--width", "--block-size", "--quoting-style"},
            long_optval={"--color"}, content=False),
    "cat": R(letters="nbsAETvetu", longs={"--number", "--number-nonblank", "--squeeze-blank",
                                           "--show-all", "--show-ends", "--show-tabs", "--show-nonprinting"}),
    "head": R(letters="qvz", short_val="nc", numeric=True,
              long_val={"--lines", "--bytes"}, longs={"--quiet", "--silent", "--verbose", "--zero-terminated"}),
    "tail": R(letters="qvzfF", short_val="ncs", numeric=True,
              long_val={"--lines", "--bytes", "--sleep-interval", "--pid", "--max-unchanged-stats"},
              longs={"--quiet", "--silent", "--verbose", "--retry", "--zero-terminated"},
              long_optval={"--follow"}),
    "wc": R(letters="lwcmL", longs={"--lines", "--words", "--bytes", "--chars", "--max-line-length"},
            long_val={"--files0-from"}, read_opts={"--files0-from"}),
    "grep": _GREP, "egrep": _GREP, "fgrep": _GREP,
    "rg": R(letters="inlcwFSsvxoHNLuUzaIq0pb", short_val="etTgABCmjMEfr",
            longs={"--hidden", "--no-ignore", "--files", "--count", "--files-with-matches", "--ignore-case",
                   "--smart-case", "--fixed-strings", "--word-regexp", "--line-number", "--no-heading",
                   "--heading", "--json", "--vimgrep", "--follow", "--multiline", "--only-matching",
                   "--invert-match", "--count-matches", "--stats", "--trim", "--no-messages", "--null",
                   "--case-sensitive", "--no-line-number", "--with-filename", "--no-filename"},
            long_val={"--glob", "--iglob", "--type", "--type-not", "--max-count", "--after-context",
                      "--before-context", "--context", "--max-depth", "--threads", "--regexp", "--file",
                      "--sort", "--sortr", "--max-columns", "--replace", "--colors"},
            long_optval={"--color"},
            pattern_first=True, pattern_opts={"-e", "--regexp", "-f", "--file", "--files"},
            read_opts={"-f", "--file"}, glob_opts={"-g", "--glob", "--iglob"},
            always_recursive=True, skips_hidden=True, hidden_opts={"--hidden", "-u", "-."}),
    "tree": R(letters="adfilsphugDFrtvUNCJQAxS", short_val="LPI",
              longs={"--dirsfirst", "--gitignore", "--noreport", "--du", "--prune"},
              long_val={"--filelimit", "--charset"}, content=False),
    "file": R(letters="bLhizkNnr", short_val="f",
              longs={"--brief", "--mime", "--mime-type", "--dereference"}, read_opts={"-f"}),
    "stat": R(letters="Lft", short_val="c", long_val={"--format", "--printf"},
              longs={"--dereference", "--file-system", "--terse"}, content=False),
    "du": R(letters="shacxkmLHDPbl0", short_val="dBt",
            long_val={"--max-depth", "--block-size", "--threshold", "--exclude", "--time-style"},
            longs={"--summarize", "--human-readable", "--all", "--total", "--apparent-size", "--one-file-system"},
            long_optval={"--time"}, content=False),
    "df": R(letters="hHTiklaP", short_val="tx",
            longs={"--human-readable", "--all", "--inodes", "--local", "--portability", "--total", "--print-type"},
            long_val={"--type", "--exclude-type", "--block-size"}, long_optval={"--output"}, content=False),
    "diff": R(letters="uNrqwbBiyacEdtTsp", short_val="UCWI",
              longs={"--recursive", "--new-file", "--brief", "--ignore-all-space", "--ignore-space-change",
                     "--ignore-blank-lines", "--ignore-case", "--side-by-side", "--text",
                     "--report-identical-files", "--minimal", "--strip-trailing-cr"},
              long_val={"--label", "--ignore-matching-lines", "--width", "--exclude", "--exclude-from"},
              long_optval={"--color", "--unified", "--context"},
              read_opts={"--exclude-from"}, recursive_opts={"-r", "--recursive"}),
    "cmp": R(letters="lsb", short_val="in", longs={"--verbose", "--quiet", "--silent", "--print-bytes"},
             long_val={"--ignore-initial", "--bytes"}),
    "comm": R(letters="123iz", longs={"--check-order", "--nocheck-order", "--total", "--zero-terminated"},
              long_val={"--output-delimiter"}),
    "sort": R(letters="bdfghiMnRrsuVcCmz", short_val="ktSoT",
              longs={"--numeric-sort", "--reverse", "--unique", "--human-numeric-sort", "--ignore-case",
                     "--version-sort", "--stable", "--general-numeric-sort", "--month-sort", "--random-sort",
                     "--check", "--merge", "--zero-terminated", "--ignore-leading-blanks", "--dictionary-order"},
              long_val={"--key", "--field-separator", "--buffer-size", "--output", "--temporary-directory",
                        "--parallel", "--sort", "--batch-size", "--files0-from", "--random-source"},
              write_opts={"-o", "--output", "-T", "--temporary-directory"},
              read_opts={"--files0-from", "--random-source"}),
    "uniq": R(letters="cdDiuz", short_val="fsw",
              longs={"--count", "--repeated", "--ignore-case", "--unique", "--zero-terminated"},
              long_val={"--skip-fields", "--skip-chars", "--check-chars"},
              long_optval={"--all-repeated", "--group"}, max_inputs=1),
    "cut": R(letters="snz", short_val="dfcb",
             longs={"--only-delimited", "--complement", "--zero-terminated"},
             long_val={"--delimiter", "--fields", "--characters", "--bytes", "--output-delimiter"}),
    "tr": R(letters="cCdst", longs={"--complement", "--delete", "--squeeze-repeats", "--truncate-set1"},
            no_paths=True),
    "nl": R(letters="p", short_val="bhfvisnwdl"),
    "column": R(letters="tx", short_val="sco"),
    "paste": R(letters="sz", short_val="d"),
    "fold": R(letters="bs", short_val="w"),
    "fmt": R(letters="cstu", short_val="wpg", numeric=True),
    "rev": R(),
    "tac": R(letters="br", short_val="s"),
    "expand": R(letters="i", short_val="t", numeric=True),
    "unexpand": R(letters="a", short_val="t", numeric=True),
    "realpath": R(letters="eLmPqsz", long_val={"--relative-to", "--relative-base"},
                  longs={"--canonicalize-existing", "--canonicalize-missing", "--logical", "--physical",
                         "--quiet", "--strip", "--no-symlinks", "--zero"}, content=False),
    "readlink": R(letters="efmnqsvz", longs={"--canonicalize", "--canonicalize-existing",
                                             "--canonicalize-missing", "--no-newline", "--quiet",
                                             "--silent", "--verbose", "--zero"}, content=False),
    "md5sum": _SUMS, "sha1sum": _SUMS, "sha256sum": _SUMS, "sha512sum": _SUMS, "b2sum": _SUMS,
    "cksum": _SUMS, "shasum": R(letters="bctUp0", short_val="a"), "md5": R(letters="pqrt", short_val="s"),
    "hexdump": R(letters="bcCdovx", short_val="nsef", read_opts={"-f"}),
    "xxd": R(letters="abCEeiprud", short_val="cglosn", max_inputs=1),
    "od": R(letters="bcdfilosxv", short_val="AjNtwS"),
    "strings": R(letters="adw", short_val="nte", numeric=True),
    "test": R(any_flags=True), "[": R(any_flags=True),
    "ps": R(any_flags=True, no_paths=True), "free": R(any_flags=True, no_paths=True),
    "bat": R(letters="pnAP", short_val="lrH",
             long_val={"--paging", "--language", "--style", "--theme", "--line-range", "--highlight-line",
                       "--wrap", "--decorations"}, long_optval={"--color"}),
}


def _judge_read(name: str, args: list[str], ctx: Context) -> Decision:
    rs = READ_SPECS[name]
    try:
        opts, pos = scan(args, rs.opts)
    except Unclear as e:
        return Decision(ASK, f"{name}：{e}（只读白名单以外的用法），请确认")
    names = {o for o, _ in opts}
    decisions = []
    for o, v in opts:
        if v is None:
            continue
        if o in rs.write_opts:
            decisions.append(path_rule(v, ctx, "write"))
        elif o in rs.read_opts:
            decisions.append(path_rule(v, ctx, "read"))
        elif o in rs.glob_opts and glob_may_name_secret(v.lstrip("!")):
            decisions.append(Decision(DENY, f"{name} 的档名样式会对到机密档案：{v}"))
    if rs.pattern_first and not names & rs.pattern_opts:
        pos = pos[1:]
    if rs.no_paths:
        pos = []
    if rs.max_inputs is not None and len(pos) > rs.max_inputs:
        decisions += [path_rule(p, ctx, "write") for p in pos[rs.max_inputs:]]
        pos = pos[:rs.max_inputs]
    recursive = rs.always_recursive or bool(names & rs.recursive_opts) or any(
        o in ("-d", "--directories") and v == "recurse" for o, v in opts)
    reads_content = rs.content and not (name == "rg" and "--files" in names)
    if recursive and not pos:
        pos = ["."]
    globs = [v for o, v in opts if o in rs.glob_opts and v and not v.startswith("!")]
    only = globs[0] if len(globs) == 1 else None
    hidden = not rs.skips_hidden or bool(names & rs.hidden_opts) or bool(globs)
    for p in pos:
        decisions.append(path_rule(p, ctx, "read"))
        if recursive and reads_content:
            decisions.append(recursive_read_rule(p, ctx, include_hidden=hidden, only=only))
    return worst(decisions)


# --- 2. 专门规则 -----------------------------------------------------------------------

def _judge_cd(args, ctx, piped, depth):
    return Decision(ALLOW, "切换目录")


def _judge_rm(args, ctx, piped, depth):
    for a in args:
        if a in ("--recursive", "--no-preserve-root") or (
                a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a)):
            return Decision(DENY, "远端模式禁止递归删除（rm -r / rm -rf）")
    if not list(path_args(args)):
        return Decision(ASK, "删除档案")
    return check_paths(args, ctx, "write", destructive=True)


def _judge_chmod(args, ctx, piped, depth):
    if any(a in ("-R", "--recursive") or (a.startswith("-") and not a.startswith("--") and "R" in a)
           for a in args):
        return Decision(DENY, "远端模式禁止递归改权限（chmod -R）")
    return check_paths(args, ctx, "write", destructive=True)


FIND_ZERO = {"-print", "-print0", "-prune", "-empty", "-readable", "-writable", "-executable", "-not",
             "!", "(", ")", "-o", "-or", "-a", "-and", "-true", "-false", "-depth", "-follow", "-xdev",
             "-mount", "-nouser", "-nogroup", "-quit", "-ls", "-noleaf", "-daystart", ","}
FIND_ONE = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
            "-type", "-xtype", "-maxdepth", "-mindepth", "-size", "-mtime", "-mmin", "-atime", "-amin",
            "-ctime", "-cmin", "-user", "-group", "-perm", "-links", "-inum", "-uid", "-gid", "-used",
            "-printf", "-regextype", "-lname", "-ilname", "-newermt"}
FIND_PATH = {"-samefile", "-newer", "-anewer", "-cnewer"}


def _judge_find(args, ctx, piped, depth):
    decisions = []
    i = 0
    while i < len(args) and args[i] in ("-L", "-H", "-P"):
        i += 1
    starts = []
    while i < len(args) and not args[i].startswith("-") and args[i] not in ("(", "!", ")"):
        starts.append(args[i])
        i += 1
    decisions += [path_rule(s, ctx, "read") for s in starts or ["."]]
    while i < len(args):
        a = args[i]
        if a == "-delete":
            return Decision(DENY, "远端模式禁止 find -delete")
        if a in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            sub = []
            while j < len(args) and args[j] not in (";", "+"):
                sub.append(bash.UNKNOWN_ARG if "{}" in args[j] else args[j])
                j += 1
            decisions.append(bash.judge_argv(sub, ctx, depth=depth + 1))
            i = j + 1
            continue
        if a in ("-fprint", "-fprint0", "-fls"):
            decisions.append(path_rule(args[i + 1] if i + 1 < len(args) else bash.UNKNOWN_ARG, ctx, "write"))
            i += 2
            continue
        if a == "-fprintf":
            decisions.append(path_rule(args[i + 1] if i + 1 < len(args) else bash.UNKNOWN_ARG, ctx, "write"))
            i += 3
            continue
        if a in FIND_PATH:
            decisions.append(path_rule(args[i + 1] if i + 1 < len(args) else bash.UNKNOWN_ARG, ctx, "read"))
            i += 2
            continue
        if a in FIND_ONE:
            i += 2
            continue
        if a in FIND_ZERO:
            i += 1
            continue
        decisions.append(Decision(ASK, f"find：不认得的条件 {a}，请确认"))
        i += 1
    return worst(decisions)


# --- git ---------------------------------------------------------------------------

GIT_GLOBAL_FLAGS = {"--no-pager", "-P", "--no-replace-objects", "--literal-pathspecs", "--glob-pathspecs",
                    "--noglob-pathspecs", "--icase-pathspecs", "--no-optional-locks", "--bare"}
GIT_GLOBAL_PATH = {"-C", "--git-dir", "--work-tree"}
GIT_RISKY_CONFIG = ("hookspath", "fsmonitor", "sshcommand", "alias.", "credential", "editor", "pager",
                    "external", "textconv", "filter.", "gitproxy", "askpass", "exec", "program", "cmd",
                    "command", "tool")
GIT_READ = {"status", "diff", "log", "show", "blame", "ls-files", "rev-parse", "describe", "shortlog",
            "grep", "ls-tree", "cat-file", "whatchanged", "show-ref", "count-objects", "merge-base",
            "name-rev", "rev-list", "diff-tree", "diff-files", "diff-index", "for-each-ref", "check-ignore"}


def _judge_git(args, ctx, piped, depth):
    decisions = []
    repo_words = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i]
        name, eq, val = opt.partition("=")
        if name in ("--version", "--help", "-h"):
            return Decision(ALLOW, "只读")
        if name == "-c":
            if i + 1 >= len(args):
                return Decision(DENY, "git -c 缺少设定")
            if any(k in args[i + 1].lower() for k in GIT_RISKY_CONFIG):
                return Decision(DENY, f"远端模式禁止用 -c 改会执行程式的 git 设定：{args[i + 1]}")
            decisions.append(Decision(ASK, f"临时改 git 设定：{args[i + 1]}"))
            i += 2
            continue
        if name == "--exec-path":
            if eq:
                return Decision(DENY, "远端模式禁止指定 git 执行程式的位置")
            return Decision(ALLOW, "只读")
        if name == "--config-env":
            return Decision(DENY, "远端模式禁止 git --config-env")
        if name in ("-p", "--paginate"):
            decisions.append(Decision(ASK, "git 会开分页程式"))
            i += 1
            continue
        if name in GIT_GLOBAL_PATH or name == "--namespace":
            if not eq and i + 1 >= len(args):
                return Decision(DENY, f"{name} 缺少值")
            if name in GIT_GLOBAL_PATH:
                repo_words.append(val if eq else args[i + 1])
            i += 1 if eq else 2
            continue
        if name in GIT_GLOBAL_FLAGS:
            i += 1
            continue
        return Decision(ASK, f"不认得的 git 选项 {opt}，请确认")
    if i >= len(args):
        return worst(decisions)
    sub, rest = args[i], args[i + 1:]
    flags = set(rest)

    def deny(why):
        return Decision(DENY, f"远端模式禁止 git {sub}：{why}")

    # 任何子指令：写出档案的选项要检查路径；会开外部程式的选项拒绝
    for k, a in enumerate(rest):
        key, eq, val = a.partition("=")
        if key in ("--output", "-o", "--output-directory") and sub not in ("checkout", "branch", "tag"):
            target = val if eq else (rest[k + 1] if k + 1 < len(rest) else "")
            if key != "-o" or sub in ("format-patch", "archive"):
                decisions.append(path_rule(target or bash.UNKNOWN_ARG, ctx, "write"))
        if key in ("--open-files-in-pager", "--upload-pack", "--receive-pack", "--exec", "--extcmd",
                   "--tool", "-x") or (sub == "grep" and a.startswith("-O")):
            return deny(f"{key} 会执行外部程式")
        if key in ("--ext-diff", "--textconv"):
            decisions.append(Decision(ASK, f"git {sub} {key} 会执行设定里的外部程式"))

    if sub == "push":
        return deny("会推送到远端，无法从电脑这边收回")
    if sub in ("filter-branch", "filter-repo"):
        return deny("改写整个历史")
    if sub == "reset" and "--hard" in flags:
        return deny("会丢掉未 commit 的修改")
    if sub == "clean" and any(f == "--force" or (f.startswith("-") and not f.startswith("--") and "f" in f)
                              for f in rest):
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
            return worst(decisions, Decision(ALLOW, "只读"))
        return deny("修改 git 设定可能让程式之后自动执行")
    if sub == "submodule" and rest[:1] == ["deinit"]:
        return deny("移除子模组")
    if sub == "worktree" and rest[:1] == ["remove"] and ("--force" in flags or "-f" in flags):
        return deny("强制移除工作树")
    if sub in ("difftool", "mergetool", "send-email", "daemon", "instaweb", "credential", "svn", "p4"):
        return deny("会执行外部程式或连到外部")

    read_only = (sub in GIT_READ
                 or (sub == "branch" and (not rest or flags <= {"-a", "-r", "--list", "-v", "-vv", "--all"}))
                 or (sub == "remote" and (not rest or rest[0] in ("-v", "show", "get-url")))
                 or (sub == "stash" and rest[:1] in (["list"], ["show"]))
                 or (sub == "tag" and (not rest or flags <= {"-l", "--list"}))
                 or (sub == "reflog" and (not rest or rest[0] == "show")))

    repo_cls = [classify_path(w, ctx) for w in repo_words]
    if read_only:
        if any(c in (OUTSIDE, UNKNOWN) for c in repo_cls):
            decisions.append(Decision(ASK, "读取工作目录外的 repo"))
        return worst(decisions, Decision(ALLOW, "只读"))
    if any(c != INSIDE for c in repo_cls):
        return Decision(DENY, "远端模式禁止修改工作目录外的 repo")
    return worst(decisions, Decision(ASK, f"git {sub} 会修改 repo"))


# --- 网络 -------------------------------------------------------------------------

def _split_opts(args, short_val: str, long_val: set):
    """curl / wget 用：把 -XPOST、-o/x、--output x、--output=x 都拆成 (名称, 值)。"""
    out = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if name in long_val and not eq:
                val = args[i + 1] if i + 1 < len(args) else ""
                i += 1
            out.append((name, val if (eq or name in long_val) else None))
        elif a.startswith("-") and len(a) > 1:
            for j in range(1, len(a)):
                ch = a[j]
                if ch in short_val:
                    val = a[j + 1:]
                    if not val:
                        val = args[i + 1] if i + 1 < len(args) else ""
                        i += 1
                    out.append(("-" + ch, val))
                    break
                out.append(("-" + ch, None))
        i += 1
    return out


CURL_SHORT_VAL = "AbcCdDeEFHKmoPQrTtuUwxXyYz"
CURL_LONG_VAL = {"--data", "--data-binary", "--data-raw", "--data-urlencode", "--data-ascii", "--form",
                 "--form-string", "--json", "--upload-file", "--config", "--request", "--output",
                 "--dump-header", "--cookie-jar", "--output-dir", "--trace", "--trace-ascii", "--stderr",
                 "--libcurl", "--header", "--user-agent", "--user", "--referer", "--cookie", "--max-time",
                 "--connect-timeout", "--proxy", "--range", "--write-out", "--retry", "--cacert", "--cert",
                 "--key", "--quote", "--mail-from", "--mail-rcpt", "--url", "--etag-save", "--etag-compare",
                 "--resolve", "--interface", "--limit-rate", "--max-filesize", "--variable"}
CURL_SEND = {"-d", "--data", "--data-binary", "--data-raw", "--data-urlencode", "--data-ascii", "-F",
             "--form", "--form-string", "--json", "-T", "--upload-file", "-K", "--config", "-Q", "--quote",
             "--mail-from", "--mail-rcpt", "--variable"}
CURL_WRITE = {"-o", "--output", "-D", "--dump-header", "-c", "--cookie-jar", "--output-dir", "--trace",
              "--trace-ascii", "--stderr", "--libcurl", "--etag-save"}


def _judge_curl(args, ctx, piped, depth):
    decisions = [Decision(ASK, "从网络下载")]
    for name, val in _split_opts(args, CURL_SHORT_VAL, CURL_LONG_VAL):
        if name in CURL_SEND:
            return Decision(DENY, f"远端模式禁止用 curl 送资料到外部（{name}）")
        if name in ("-X", "--request") and (val or "").upper() not in ("GET", "HEAD"):
            return Decision(DENY, f"远端模式禁止 curl {val} 请求")
        if name in CURL_WRITE and val and val != "-":
            decisions.append(path_rule(val, ctx, "write"))
    return worst(decisions)


WGET_SHORT_VAL = "OoaPiUeBTtwQlADRXI"
WGET_LONG_VAL = {"--output-document", "--output-file", "--append-output", "--directory-prefix",
                 "--input-file", "--user-agent", "--execute", "--base", "--timeout", "--tries", "--wait",
                 "--quota", "--level", "--accept", "--reject", "--domains", "--exclude-directories",
                 "--include-directories", "--post-data", "--post-file", "--method", "--body-data",
                 "--body-file", "--config", "--save-cookies", "--load-cookies", "--warc-file", "--header",
                 "--user", "--password", "--use-askpass"}
WGET_SEND = {"--post-data", "--post-file", "--method", "--body-data", "--body-file", "-e", "--execute",
             "--config", "--use-askpass"}
WGET_WRITE = {"-O", "--output-document", "-o", "--output-file", "-a", "--append-output", "-P",
              "--directory-prefix", "--save-cookies", "--warc-file"}


def _judge_wget(args, ctx, piped, depth):
    decisions = [Decision(ASK, "从网络下载")]
    for name, val in _split_opts(args, WGET_SHORT_VAL, WGET_LONG_VAL):
        if name in WGET_SEND:
            return Decision(DENY, f"远端模式禁止 wget {name}（送资料到外部或执行设定）")
        if name in WGET_WRITE and val and val != "-":
            decisions.append(path_rule(val, ctx, "write"))
    return worst(decisions)


def _judge_rsync(args, ctx, piped, depth):
    for a in args:
        key = a.split("=", 1)[0]
        if key.startswith("--del") or key == "--remove-source-files":
            return Decision(DENY, f"远端模式禁止 rsync {key}（会删除档案）")
        if key in ("--rsh", "--rsync-path") or (a.startswith("-") and not a.startswith("--") and "e" in a):
            return Decision(DENY, "远端模式禁止 rsync 指定远端程式")
        if not a.startswith("-") and ("::" in a or "rsync://" in a or re.match(r"^[^/]+:", a)):
            return Decision(DENY, "远端模式禁止 rsync 到其他机器")
    return check_paths(args, ctx, "write")


def _judge_kill(args, ctx, piped, depth):
    for i, a in enumerate(args):
        nxt = args[i + 1].upper() if i + 1 < len(args) else ""
        if a.upper() in ("-9", "-KILL", "-SIGKILL", "-S9", "-SKILL", "-SSIGKILL"):
            return Decision(DENY, "远端模式禁止 kill -9")
        if a in ("-s", "-n") and nxt in ("KILL", "SIGKILL", "9"):
            return Decision(DENY, "远端模式禁止 kill -9")
    return Decision(ASK, "结束程式")


GH_READ = {("pr", "view"), ("pr", "list"), ("pr", "diff"), ("pr", "status"), ("pr", "checks"),
           ("issue", "view"), ("issue", "list"), ("repo", "view"), ("run", "list"), ("run", "view"),
           ("status", None)}


def _judge_gh(args, ctx, piped, depth):
    key = (args[0] if args else None, args[1] if len(args) > 1 else None)
    if key in GH_READ or (key[0], None) in GH_READ:
        return Decision(ALLOW, "只读")
    return Decision(DENY, "远端模式禁止用 gh 修改 GitHub 上的东西")


# --- sed ---------------------------------------------------------------------------

_SED_ADDR = re.compile(r"\s*(\d+|\$|/(?:\\.|[^/\\])*/I?|\\(.)(?:\\.|(?!\2).)*\2I?)(~\d+)?")


def sed_actions(script: str) -> list[tuple[str, str]]:
    """把 sed 脚本拆成 [(动作, 参数)]：exec（执行指令）/ write / read。看不懂时丢 ValueError。"""
    actions = []
    i, n = 0, len(script)

    def addr(i):
        m = _SED_ADDR.match(script, i)
        return m.end() if m else i

    while i < n:
        while i < n and script[i] in " \t\n;{}":
            i += 1
        if i >= n:
            break
        if script[i] == "#":
            j = script.find("\n", i)
            i = n if j < 0 else j
            continue
        i = addr(i)
        if i < n and script[i] == ",":
            i = addr(i + 1)
        while i < n and script[i] in " \t!":
            i += 1
        if i >= n:
            raise ValueError("缺少指令")
        cmd = script[i]
        i += 1
        if cmd in "pdDnNPqQ=hHgGxlzFb tT:":
            if cmd in "bTt:":
                while i < n and script[i] not in ";\n}":
                    i += 1
            elif cmd in "qQl":
                while i < n and script[i].isdigit():
                    i += 1
            continue
        if cmd in "aic":
            j = script.find("\n", i)
            i = n if j < 0 else j
            continue
        if cmd in "sy":
            if i >= n:
                raise ValueError("s 缺少分隔符")
            delim = script[i]
            parts, buf, i = [], "", i + 1
            while i < n and len(parts) < 2:
                ch = script[i]
                if ch == "\\" and i + 1 < n:
                    buf += script[i:i + 2]
                    i += 2
                    continue
                if ch == delim:
                    parts.append(buf)
                    buf = ""
                else:
                    buf += ch
                i += 1
            if len(parts) < 2:
                raise ValueError("s 指令不完整")
            if cmd == "s":
                while i < n and script[i] in "gGpPiImMe0123456789":
                    if script[i] == "e":
                        actions.append(("exec", "s///e"))
                    i += 1
                if i < n and script[i] == "w":
                    j = script.find("\n", i)
                    target = script[i + 1:(n if j < 0 else j)].strip()
                    actions.append(("write", target))
                    i = n if j < 0 else j
            continue
        if cmd in "eE":
            j = script.find("\n", i)
            actions.append(("exec", script[i:(n if j < 0 else j)].strip()))
            i = n if j < 0 else j
            continue
        if cmd in "wWrR":
            j = script.find("\n", i)
            target = script[i:(n if j < 0 else j)].strip()
            actions.append(("write" if cmd in "wW" else "read", target))
            i = n if j < 0 else j
            continue
        raise ValueError(f"不认得的 sed 指令 {cmd}")
    return actions


SED_SPEC = spec(letters="nrEsuz", short_val="ef", long_optval={"--in-place"},
                longs={"--quiet", "--silent", "--regexp-extended", "--separate", "--unbuffered",
                       "--null-data", "--posix", "--sandbox", "--debug"},
                long_val={"--expression", "--file", "--line-length"})


def _judge_sed(args, ctx, piped, depth):
    in_place = any(a == "-i" or (a.startswith("-i") and not a.startswith("--")) or a.startswith("--in-place")
                   or (a.startswith("-") and not a.startswith("--") and "i" in a[1:]) for a in args)
    cleaned = [a for a in args if not (a.startswith("-i") or a.startswith("--in-place"))]
    cleaned = [a.replace("i", "") if a.startswith("-") and not a.startswith("--") and "i" in a else a
               for a in cleaned]
    cleaned = [a for a in cleaned if a != "-"]
    try:
        opts, pos = scan(cleaned, SED_SPEC)
    except Unclear as e:
        return Decision(ASK, f"sed：{e}，请确认")
    scripts = [v for o, v in opts if o in ("-e", "--expression")]
    script_files = [v for o, v in opts if o in ("-f", "--file")]
    if not scripts and not script_files:
        if not pos:
            return Decision(ASK, "sed 缺少脚本")
        scripts, pos = [pos[0]], pos[1:]
    decisions = [path_rule(f, ctx, "read") for f in script_files]
    if script_files:
        decisions.append(Decision(ASK, "sed 从档案读取脚本，内容无法确认"))
    for s in scripts:
        try:
            actions = sed_actions(s)
        except ValueError as e:
            decisions.append(Decision(ASK, f"看不懂 sed 脚本（{e}），请确认"))
            continue
        for kind, target in actions:
            if kind == "exec":
                return Decision(DENY, "远端模式禁止 sed 执行指令（e 指令或 s///e）")
            if target in ("/dev/stdout", "/dev/stderr"):
                continue
            decisions.append(path_rule(target, ctx, kind))
    mode = "write" if in_place else "read"
    decisions += [path_rule(p, ctx, mode) for p in pos]
    return worst(decisions, Decision(ASK if in_place else ALLOW, "会修改档案" if in_place else "只读"))


# --- shell / 直译器 ---------------------------------------------------------------------

def _judge_shell(args, ctx, piped, depth):
    i, has_c, stdin_flag = 0, False, False
    while i < len(args):
        a = args[i]
        if a in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 2
            continue
        if a == "--":
            i += 1
            break
        if a.startswith("--"):
            i += 1
            continue
        if a[:1] in ("-", "+") and len(a) > 1:
            if a[0] == "-" and "c" in a[1:]:
                has_c = True
            if a[0] == "-" and "s" in a[1:]:
                stdin_flag = True
            i += 1
            continue
        break
    rest = args[i:]
    if has_c:
        if not rest:
            return Decision(DENY, "看不到 -c 要执行的内容")
        return bash.analyze_bash(rest[0], ctx, depth + 1)
    if stdin_flag or not rest:
        return Decision(DENY, "远端模式禁止让 shell 从输入读取程式（例如 curl ... | sh）")
    return check_paths(rest, ctx, "other")


INTERP_VALUE_OPTS = {"-W", "-X", "-I", "-M", "-r", "--require", "--input-type", "-C", "-l", "-d"}
INTERP_INLINE = {"python": "c", "perl": "eE", "ruby": "e", "node": "ep", "php": "r", "deno": "",
                 "bun": "e", "lua": "e", "rscript": "e"}


def _judge_interpreter(name, args, ctx, piped, depth):
    base = "python" if name.startswith("python") else name
    inline_letters = INTERP_INLINE.get(base, "")
    reads_stdin = True
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-":
            break
        if not a.startswith("-"):
            reads_stdin = False
            break
        if a in ("--eval", "--print") or (len(a) > 1 and a[1:2] in inline_letters and not a.startswith("--")):
            reads_stdin = False
            break
        if base == "python" and a.startswith("-m"):
            reads_stdin = False
            break
        if a in INTERP_VALUE_OPTS:
            i += 2
            continue
        i += 1
    if piped and reads_stdin:
        return Decision(DENY, f"远端模式禁止把前一个指令的输出直接交给 {name} 执行")
    hidden = bash.embedded_danger(args, ctx, depth)
    if hidden:
        return hidden
    return check_paths(args, ctx, "other")


SPECIAL = {
    "cd": _judge_cd, "pushd": _judge_cd, "popd": _judge_cd, "rm": _judge_rm, "chmod": _judge_chmod,
    "find": _judge_find, "git": _judge_git, "curl": _judge_curl, "wget": _judge_wget,
    "rsync": _judge_rsync, "kill": _judge_kill, "sed": _judge_sed, "gsed": _judge_sed, "gh": _judge_gh,
}
