"""路径分类：一个路径（或一个含 {a,b}、* 的样式）属于哪一类。

  SENSITIVE  机密（~/.ssh、.env、id_rsa……）       读写都 Deny
  PROTECTED  受保护（安全层本身、.git/hooks……）    写 Deny，读 Allow
  INSIDE     工作目录内                            读 Allow，写 Ask
  SCRATCH    /tmp 等暂存区                         读 Allow，写 Ask
  OUTSIDE    其他地方                              读 Ask，写 Deny
  UNKNOWN    无法确定（含变数、太多档案没看完）     读 Ask，写 Deny

展开万用字元和扫描资料夹都有数量上限（LIMIT），避免 hook 跑太久。
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from .core import ALLOW, ASK, DENY, Context, Decision, real, worst

SENSITIVE, PROTECTED, INSIDE, SCRATCH, OUTSIDE, UNKNOWN = (
    "sensitive", "protected", "inside", "scratch", "outside", "unknown")
SEVERITY = {INSIDE: 0, SCRATCH: 1, OUTSIDE: 2, UNKNOWN: 3, PROTECTED: 4, SENSITIVE: 5}

SENSITIVE_NAME_RE = re.compile(
    r"^(\.env(\..+)?|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|.+\.(pem|p12|pfx|key|keychain|keychain-db)"
    r"|\.netrc|\.git-credentials|\.npmrc|\.pypirc|credentials(\.json)?)$"
)
SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}
SENSITIVE_SAMPLES = (".env", ".env.local", "id_rsa", "id_ed25519", "a.pem", "a.key", ".netrc",
                     ".npmrc", "credentials")
DEVICE_SINKS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
GLOB_CHARS = "*?["
LIMIT = 5000          # 展开 / 扫描最多看几个目录项目
BRACE_LIMIT = 64      # {a,b} 最多展开几种


def is_inside(p: Path, base: Path) -> bool:
    return p == base or base in p.parents


def sensitive_name(name: str) -> bool:
    return name not in SAFE_ENV_NAMES and bool(SENSITIVE_NAME_RE.match(name))


def glob_may_name_secret(pattern: str) -> bool:
    """只看档名样式：.e*、*.pem、{.env,x} 这类会不会对到机密档名。"""
    for name in expand_braces(pattern) or [pattern]:
        name = name.rstrip("/").rsplit("/", 1)[-1]
        if sensitive_name(name):
            return True
        if any(fnmatch.fnmatchcase(s, name) for s in SENSITIVE_SAMPLES):
            return True
    return False


# --- {a,b} 和 {1..3} 展开 ---------------------------------------------------------

_BRACE_LIST = re.compile(r"\{([^{}]*,[^{}]*)\}")
_BRACE_RANGE = re.compile(r"\{(-?\w+)\.\.(-?\w+)\}")


def expand_braces(word: str) -> list[str] | None:
    """照 bash 的规则展开大括号；展开太多时回传 None。"""
    out = [word]
    while True:
        changed, nxt = False, []
        for w in out:
            m = _BRACE_LIST.search(w)
            if m:
                alts = m.group(1).split(",")
            else:
                m = _BRACE_RANGE.search(w)
                if not m:
                    nxt.append(w)
                    continue
                a, b = m.group(1), m.group(2)
                if a.lstrip("-").isdigit() and b.lstrip("-").isdigit():
                    lo, hi = int(a), int(b)
                    step = 1 if hi >= lo else -1
                    if abs(hi - lo) > BRACE_LIMIT:
                        return None
                    alts = [str(i) for i in range(lo, hi + step, step)]
                elif len(a) == 1 and len(b) == 1:
                    step = 1 if ord(b) >= ord(a) else -1
                    alts = [chr(i) for i in range(ord(a), ord(b) + step, step)]
                else:
                    nxt.append(w)
                    continue
            changed = True
            nxt.extend(w[:m.start()] + alt + w[m.end():] for alt in alts)
        out = nxt
        if len(out) > BRACE_LIMIT:
            return None
        if not changed:
            return out


# --- 把一个字变成绝对路径 -------------------------------------------------------------

def expand_home(word: str, ctx: Context) -> str:
    for var in ("${HOME}", "$HOME"):
        if word.startswith(var):
            return str(ctx.home) + word[len(var):]
    if word == "~" or word.startswith("~/"):
        return str(ctx.home) + word[1:]
    return word


def absolute(word: str, ctx: Context) -> str | None:
    """展开 ~ 和 $HOME，接上目前目录；有变数或 ~user 时回传 None。不处理万用字元。"""
    w = expand_home(word, ctx)
    if "$" in w or "`" in w or w.startswith("~"):
        return None
    if not os.path.isabs(w):
        if ctx.cwd is None:
            return None
        w = os.path.join(str(ctx.cwd), w)
    return w


def resolve_word(word: str, ctx: Context) -> Path | None:
    """一个字（无万用字元）→ 解开 symlink 后的绝对路径；无法确定时 None。"""
    w = absolute(word, ctx)
    return real(w) if w is not None else None


def bounded_glob(pattern: str, limit: int = LIMIT) -> tuple[list[str], bool]:
    """展开绝对路径的万用字元，最多看 limit 个目录项目。回传 (结果, 是否看完)。"""
    budget = limit
    paths = ["/"]
    parts = [p for p in pattern.split("/") if p]
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        nxt: list[str] = []
        for base in paths:
            if part == "**":
                for root, dirs, files in os.walk(base):
                    budget -= len(dirs) + len(files) + 1
                    if budget < 0:
                        return [], False
                    nxt.append(root)
                    if last:
                        nxt.extend(os.path.join(root, f) for f in files)
            elif any(c in part for c in GLOB_CHARS):
                try:
                    entries = os.listdir(base)
                except OSError:
                    continue
                budget -= len(entries)
                if budget < 0:
                    return [], False
                for e in entries:
                    if e.startswith(".") and not part.startswith("."):
                        continue  # bash 的 * 不对到隐藏档
                    if fnmatch.fnmatchcase(e, part):
                        p = os.path.join(base, e)
                        if last or os.path.isdir(p):
                            nxt.append(p)
            else:
                p = os.path.join(base, part)
                if os.path.lexists(p):
                    nxt.append(p)
        paths = nxt
        if not paths:
            break
    return paths, True


# --- 分类 -------------------------------------------------------------------------

def classify_resolved(p: Path, ctx: Context) -> str:
    if sensitive_name(p.name) or any(is_inside(p, s) for s in ctx.sensitive_dirs):
        return SENSITIVE
    if any(is_inside(p, s) for s in ctx.protected):
        return PROTECTED
    if is_inside(p, ctx.work_dir):
        return INSIDE
    if str(p) in DEVICE_SINKS or any(is_inside(p, s) for s in ctx.scratch):
        return SCRATCH
    return OUTSIDE


def targets(word: str, ctx: Context) -> list[Path] | None:
    """一个字实际指到哪些路径（展开 {} 和万用字元）。无法确定时 None。"""
    variants = expand_braces(word)
    if variants is None:
        return None
    result: list[Path] = []
    for v in variants:
        if v in DEVICE_SINKS:
            result.append(Path(v))
            continue
        a = absolute(v, ctx)
        if a is None:
            return None
        if any(c in v for c in GLOB_CHARS):
            matches, complete = bounded_glob(a)
            if not complete:
                return None
            result.extend(real(m) for m in matches)
        else:
            result.append(real(a))
    return result


def _static_dir(pattern_abs: str) -> Path:
    """万用字元前面确定的那段资料夹。"""
    cut = min((pattern_abs.find(c) for c in GLOB_CHARS if c in pattern_abs), default=len(pattern_abs))
    return real(pattern_abs[:cut].rsplit("/", 1)[0] or "/")


def classify_path(word: str, ctx: Context) -> str:
    """一个字（可能含 {a,b}、万用字元）最严重属于哪一类。"""
    variants = expand_braces(word)
    if variants is None:
        return UNKNOWN
    worst_cls = INSIDE
    for v in variants:
        cls = _classify_variant(v, ctx)
        if SEVERITY[cls] > SEVERITY[worst_cls]:
            worst_cls = cls
    return worst_cls


def _classify_variant(word: str, ctx: Context) -> str:
    name = word.rstrip("/").rsplit("/", 1)[-1]
    if sensitive_name(name):
        return SENSITIVE
    if word in DEVICE_SINKS:
        return SCRATCH
    has_glob = any(c in word for c in GLOB_CHARS)
    if has_glob and name.startswith(".") and glob_may_name_secret(name):
        return SENSITIVE
    a = absolute(word, ctx)
    if a is None:
        return UNKNOWN
    if not has_glob:
        return classify_resolved(real(a), ctx)
    matches, complete = bounded_glob(a)
    if not complete:
        return UNKNOWN
    classes = [classify_resolved(real(m), ctx) for m in matches] or [classify_resolved(_static_dir(a), ctx)]
    return max(classes, key=SEVERITY.get)


# --- 判断规则 ---------------------------------------------------------------------

def contains_guarded(word: str, ctx: Context) -> bool | None:
    """这个路径（删除 / 搬移的目标）是否「包含」受保护或机密的东西。None = 无法确定。"""
    found = targets(word, ctx)
    if found is None:
        return None
    guarded = ctx.protected + ctx.sensitive_dirs
    return any(is_inside(g, t) for t in found for g in guarded)


def path_rule(word: str, ctx: Context, mode: str, destructive: bool = False) -> Decision:
    """mode: read（只读）/ write（会写或删）/ other（交给别的程式）。"""
    if mode == "write" and word in DEVICE_SINKS:
        return Decision(ALLOW, "丢弃输出")
    cls = classify_path(word, ctx)
    if cls == SENSITIVE:
        return Decision(DENY, f"碰到机密档案：{word}")
    if mode == "read":
        if cls in (OUTSIDE, UNKNOWN):
            return Decision(ASK, f"读取工作目录外或无法确定的路径：{word}")
        return Decision(ALLOW, "只读")
    if cls == PROTECTED:
        return Decision(DENY, f"不能修改安全层、shell 启动档或会自动执行的设定：{word}")
    if mode == "write":
        if cls in (OUTSIDE, UNKNOWN):
            return Decision(DENY, f"不能写入 / 删除工作目录外或无法确定的路径：{word}")
        if destructive:
            g = contains_guarded(word, ctx)
            if g is None:
                return Decision(DENY, f"无法确定要删除 / 搬移哪些档案：{word}")
            if g:
                return Decision(DENY, f"这个路径包含受保护的档案：{word}")
        return Decision(ASK, f"会修改档案：{word}")
    return Decision(ASK, "执行程式")


def path_args(args):
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


def check_paths(args, ctx: Context, mode: str, destructive: bool = False) -> Decision:
    base = {"read": Decision(ALLOW, "只读"), "write": Decision(ASK, "会修改档案"),
            "other": Decision(ASK, "执行程式")}[mode]
    return worst((path_rule(a, ctx, mode, destructive) for a in path_args(args)), base)


def dir_has_secret(word: str, ctx: Context, include_hidden: bool = True,
                   only: str | None = None) -> bool | None:
    """递回读取一个资料夹时，里面有没有机密档案。None = 档案太多没看完。

    only：只搜寻符合这个档名样式的档案（rg -g '*.py'），机密档不符合就不算。
    """
    found = targets(word, ctx)
    if found is None:
        return None
    patterns = expand_braces(only) if only else None

    def counts(f: str) -> bool:
        if not sensitive_name(f):
            return False
        return patterns is None or any(fnmatch.fnmatchcase(f, p.rsplit("/", 1)[-1]) for p in patterns)

    budget = LIMIT
    for d in found:
        if classify_resolved(d, ctx) == SENSITIVE or any(is_inside(s, d) for s in ctx.sensitive_dirs):
            return True
        if not d.is_dir():
            continue
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if x != ".git" and (include_hidden or not x.startswith("."))]
            if not include_hidden:
                files = [f for f in files if not f.startswith(".")]
            budget -= len(dirs) + len(files)
            if budget < 0:
                return None
            if any(counts(f) for f in files):
                return True
    return False


def recursive_read_rule(word: str, ctx: Context, include_hidden: bool = True,
                        only: str | None = None) -> Decision:
    has = dir_has_secret(word, ctx, include_hidden, only)
    if has is None:
        return Decision(ASK, f"资料夹太大，无法确认里面没有机密档案：{word}")
    if has:
        return Decision(DENY, f"这个资料夹里有机密档案（例如 .env），请指定子资料夹或排除它：{word}")
    return Decision(ALLOW, "只读")
