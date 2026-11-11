#!/usr/bin/env python3
"""启动远端 session：选好安全等级和工作资料夹，检查通过后在 tmux 里背景执行。

  python3 scripts/start_remote.py --level normal --dir ~/Desktop/learn_git   启动
  python3 scripts/start_remote.py                                            用设定档的预设值启动
  python3 scripts/start_remote.py --boot                                     开机服务用（只看预设值）
  python3 scripts/start_remote.py --status                                   列出正在跑的远端 session
  python3 scripts/start_remote.py --stop --name Desktop_learn_git            停止某个 session（也可用 --dir；--all 全部）
  python3 scripts/start_remote.py --dry-run ...                              只显示会做什么，不真的启动

启动前会：
  1. 确认 --dir 在本机设定档的 work_dirs 白名单里（白名单内的子资料夹也可以）
  2. normal 等级：资料夹必须是 git repo 的「根目录」（git 指令会影响整个 repo，子资料夹会越界）
  3. 跑 preflight，有 ❌ 就不启动
  4. 同一个资料夹已经在跑：等级相同就不重复启动；等级不同就要求先停掉
  5. normal 等级：有未 commit 的修改时先存一个还原点
  6. 在专用的 tmux server（tmux -L remote-ctrl）里启动：移除会盖掉订阅登录的环境变量，
     设定 REMOTE_CTRL_WORK_DIR / REMOTE_CTRL_LEVEL，用 systemd-inhibit（Ubuntu）或 caffeinate（Mac）防休眠
  7. 等网络好了才启动 claude；异常结束自动重启（10 分钟内最多 5 次）

名称：预设是「上一层资料夹_资料夹名」（例如 Desktop_learn_git），手机上看到的也是这个。
tmux session 名称：rc-<名称>-<路径代码>-<等级>，例如 rc-Desktop_learn_git-3fa2c1-readonly。
路径代码来自完整路径，所以不同位置的同名资料夹不会撞在一起。

预设值写在本机设定档（Ubuntu ~/.config/remote-ctrl/config.json，Mac ~/Library/Application Support/remote-ctrl/config.json）：
  "work_dirs": ["~/Desktop/learn_git"], "default_dir": "~/Desktop/learn_git", "default_level": "readonly"

只用标准库（系统内建的 python3 就能跑）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

import audit  # noqa: E402
import gitsnap  # noqa: E402
import platform_utils as pu  # noqa: E402
from preflight import cli as preflight_cli  # noqa: E402
from preflight import config_store, runner  # noqa: E402
from preflight.models import FAIL  # noqa: E402

LEVELS = pu.LEVELS
SESSION_PREFIX = "rc-"
MAX_RESTARTS, RESTART_WINDOW, RESTART_DELAY = 5, 600, 10
NETWORK_HOST, NETWORK_WAIT = ("api.anthropic.com", 443), 300   # 开机时最多等网络 5 分钟


class StartError(Exception):
    pass


def safe_name(text: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    return name or "session"


def default_name(d: Path) -> str:
    """手机和 tmux 上看到的名称：上一层资料夹 + 资料夹名，同名的资料夹也分得出来。

    ~/Desktop/learn_git → Desktop_learn_git；~/Desktop/test_area/learn_git → test_area_learn_git
    直接放在家目录（或根目录）下的资料夹只用自己的名称：~/rc-test → rc-test
    """
    parent = d.parent
    if parent in (Path.home().resolve(), Path("/")):
        return d.name
    return f"{parent.name}_{d.name}"


def dir_code(d: Path) -> str:
    return hashlib.sha1(str(d).encode()).hexdigest()[:6]


@dataclass
class Target:
    level: str
    dir: Path
    name: str

    @property
    def base(self) -> str:
        """同一个资料夹（不分等级）的 session 名称开头。"""
        return f"{SESSION_PREFIX}{self.name}-{dir_code(self.dir)}-"

    @property
    def session(self) -> str:
        return self.base + self.level


def parse_session(session: str) -> tuple[str, str, str] | None:
    """rc-<名称>-<代码>-<等级> → (名称, 代码, 等级)；不是我们的 session 时回传 None。"""
    if not session.startswith(SESSION_PREFIX):
        return None
    parts = session[len(SESSION_PREFIX):].rsplit("-", 2)
    if len(parts) != 3 or parts[2] not in LEVELS:
        return None
    return parts[0], parts[1], parts[2]


# --- 决定等级、资料夹、名称（纯函式，方便测试） ----------------------------------------------

def allowed_dir(raw: str, work_dirs) -> Path:
    """--dir 必须存在，且在白名单里（或是白名单资料夹的子资料夹）。"""
    if not isinstance(work_dirs, list) or not work_dirs:
        raise StartError("本机设定档还没有 work_dirs 白名单。请在 config.json 加上 "
                         '"work_dirs": ["你的 repo 路径"]')
    if not Path(raw).expanduser().is_dir():
        raise StartError(f"资料夹不存在：{raw}")
    d = pu.in_whitelist(raw, work_dirs)
    if d is None:
        raise StartError(f"{Path(raw).expanduser().resolve()} 不在 work_dirs 白名单里。"
                         "要远端操作这个资料夹，请先把它加进 config.json")
    return d


def resolve_target(level: str | None, dir_: str | None, name: str | None, cfg: dict, boot: bool) -> Target:
    if boot:
        level, dir_ = cfg.get("default_level"), cfg.get("default_dir")
        if not level or not dir_:
            raise StartError('开机启动需要在 config.json 设定 "default_level" 和 "default_dir"')
    else:
        level = level or cfg.get("default_level")
        dir_ = dir_ or cfg.get("default_dir")
        if not level or not dir_:
            raise StartError("请指定 --level 和 --dir（或在 config.json 设定 default_level、default_dir）")
    if level not in LEVELS:
        raise StartError(f"不认得的安全等级 {level!r}，只能是 {' / '.join(LEVELS)}")
    d = allowed_dir(dir_, cfg.get("work_dirs"))
    return Target(level, d, safe_name(name or default_name(d)))


def check_git_for_level(target: Target) -> None:
    """normal 会改档：必须是 git repo 的根目录（才有还原点，git 指令也不会动到白名单外）。"""
    if target.level != "normal":
        return
    top = gitsnap.repo_root(target.dir)
    if top is None:
        raise StartError(f"normal 等级需要 git repo（才有还原点）：{target.dir}。请先 git init，或改用 --level readonly")
    if top.resolve() != target.dir:
        raise StartError(f"normal 等级的资料夹必须是 git repo 的根目录：{target.dir} 在 {top} 里面，"
                         "git 指令会动到整个 repo（包含白名单外的档案）。请把 repo 根目录加进白名单，或改用 --level readonly")


def session_env(base: dict, target: Target) -> dict:
    env = pu.sanitized_env(base)
    env.update(REMOTE_CTRL_WORK_DIR=str(target.dir), REMOTE_CTRL_LEVEL=target.level)
    return env


def anti_sleep_prefix() -> list[str]:
    tool = pu.find_tool(pu.anti_sleep_tool())
    if not tool:
        return []
    if pu.IS_MAC:
        return [tool, "-i"]
    return [tool, "--what=sleep:idle", "--who=remote-ctrl", "--why=Claude Code 远端 session", "--mode=block"]


def claude_command(claude: str, target: Target, settings: Path) -> list[str]:
    return anti_sleep_prefix() + [claude, "--remote-control", target.name, "--settings", str(settings)]


def tmux_base(tmux: str) -> list[str]:
    return [tmux, "-L", pu.TMUX_SOCKET]


def tmux_command(tmux: str, target: Target, python: str) -> list[str]:
    inner = [python, str(Path(__file__).resolve()), "--run", "--level", target.level,
             "--dir", str(target.dir), "--name", target.name]
    return tmux_base(tmux) + ["new-session", "-d", "-s", target.session, "-c", str(target.dir), shlex.join(inner)]


def wait_for_network(host=NETWORK_HOST, limit: float = NETWORK_WAIT, step: float = 5) -> bool:
    deadline = time.monotonic() + limit
    while True:
        try:
            with socket.create_connection(host, timeout=5):
                return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(step)


# --- tmux ---------------------------------------------------------------------------

def _tmux() -> str:
    tmux = pu.find_tool("tmux")
    if not tmux:
        raise StartError("找不到 tmux")
    return tmux


def session_paths() -> dict[str, str]:
    """正在跑的远端 session → 它的工作资料夹。"""
    p = subprocess.run(tmux_base(_tmux()) + ["list-sessions", "-F", "#{session_name}\t#{session_path}"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return {}  # 没有 tmux server = 没有 session
    result = {}
    for line in p.stdout.splitlines():
        session, _, path = line.partition("\t")
        if parse_session(session):
            result[session] = path
    return result


def running_sessions() -> list[str]:
    return list(session_paths())


def attach_hint(session: str) -> str:
    return f"tmux -L {pu.TMUX_SOCKET} attach -t {session}"


# --- 各个动作 -------------------------------------------------------------------------

def load_config() -> dict:
    cfg, err = config_store.read_local_config()
    if err:
        raise StartError(err)
    return cfg


def has_uncommitted(d: Path) -> bool:
    p = subprocess.run(["git", "-C", str(d), "status", "--porcelain"], capture_output=True, text=True)
    return p.returncode == 0 and bool(p.stdout.strip())


def start(args) -> int:
    cfg = load_config()
    target = resolve_target(args.level, args.dir, args.name, cfg, args.boot)
    print(f"安全等级：{target.level}　工作资料夹：{target.dir}　名称：{target.name}")
    check_git_for_level(target)

    checks = runner.run_checks()
    if any(c.status == FAIL for c in checks):
        preflight_cli.print_report(checks)
        raise StartError("preflight 没通过，不启动")
    print("✅ preflight 通过")

    same_dir = [s for s in running_sessions() if s.startswith(target.base)]
    if target.session in same_dir:
        print(f"ℹ️  这个资料夹已经用 {target.level} 在跑了。接上去看：{attach_hint(target.session)}")
        return 0
    if same_dir:
        other = parse_session(same_dir[0])[2]
        raise StartError(f"这个资料夹已经用 {other} 等级在跑。要换等级，先停掉："
                         f"scripts/start-remote.sh --stop --name {target.name}")

    if target.level == "normal" and has_uncommitted(target.dir):
        if args.dry_run:
            print("⚠️  有未 commit 的修改（dry-run：不存还原点）")
        else:
            cp = gitsnap.create(target.dir, "启动远端 session 前自动存档（有未 commit 的修改）")
            print(f"⚠️  有未 commit 的修改，已先存还原点 {cp.id}")

    cmd = tmux_command(_tmux(), target, sys.executable)
    if args.dry_run:
        print("（dry-run）会执行：" + shlex.join(cmd))
        return 0
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise StartError(f"tmux 启动失败：{p.stderr.strip()}")
    audit.write(pu.log_dir(), {"event": "session_start", "session": target.session, "level": target.level,
                               "work_dir": str(target.dir), "boot": args.boot})
    print(f"✅ 已在背景启动 {target.session}")
    print(f"   手机 Claude app 找名称含「{target.name}」的 session")
    print(f"   电脑上接上去看：{attach_hint(target.session)}（离开：Ctrl+B 再按 D）")
    print(f"   停止：scripts/start-remote.sh --stop --name {target.name}")
    return 0


def run_loop(args) -> int:
    """在 tmux 里面执行：等网络、启动 claude，异常结束就重启。"""
    cfg = load_config()
    target = resolve_target(args.level, args.dir, args.name, cfg, boot=False)
    check_git_for_level(target)
    claude = pu.find_tool("claude")
    if not claude:
        raise StartError("找不到 claude")
    settings = pu.config_dir() / "remote-settings.json"
    env = session_env(dict(os.environ), target)
    cmd = claude_command(claude, target, settings)
    log = pu.log_dir()
    print("⏳ 确认网络……", flush=True)
    if not wait_for_network():
        print("⚠️  网络还没好，还是先启动（失败会自动重启）", flush=True)
    restarts: list[float] = []
    while True:
        print(f"▶ {target.level} | {target.dir}\n▶ {shlex.join(cmd)}\n", flush=True)
        try:
            code = subprocess.call(cmd, env=env, cwd=target.dir)
        except KeyboardInterrupt:
            code = 0
        audit.write(log, {"event": "session_exit", "session": target.session, "code": code})
        if code == 0:
            print("claude 正常结束，不重启。")
            return 0
        now = time.time()
        restarts = [t for t in restarts if now - t < RESTART_WINDOW]
        if len(restarts) >= MAX_RESTARTS:
            print(f"❌ {RESTART_WINDOW // 60} 分钟内已重启 {MAX_RESTARTS} 次，停止。请回电脑前检查。")
            audit.write(log, {"event": "session_gave_up", "session": target.session})
            return 1
        restarts.append(now)
        print(f"⚠️  claude 异常结束（{code}），{RESTART_DELAY} 秒后重启…", flush=True)
        try:
            time.sleep(RESTART_DELAY)
            wait_for_network()
        except KeyboardInterrupt:
            return 0


def stop(args) -> int:
    sessions = running_sessions()
    if args.all:
        targets = sessions
    elif args.boot:
        target = resolve_target(None, None, None, load_config(), boot=True)   # 和启动时同一套解析
        targets = [s for s in sessions if s.startswith(target.base)]
    elif args.name or args.dir:
        targets = sessions
        if args.name:
            name = safe_name(args.name)
            targets = [s for s in targets if parse_session(s)[0] == name]
        if args.dir:
            d = Path(args.dir).expanduser().resolve()
            targets = [s for s in targets if parse_session(s)[1] == dir_code(d)]
        if len(targets) > 1:
            raise StartError(f"有 {len(targets)} 个 session 符合，请加 --dir 指定是哪个资料夹：{', '.join(targets)}")
    else:
        raise StartError("请指定 --name 或 --dir（或用 --all 停止全部）")
    if not targets:
        print("没有要停止的远端 session。")
        return 0
    for s in targets:
        subprocess.run(tmux_base(_tmux()) + ["kill-session", "-t", s])
        audit.write(pu.log_dir(), {"event": "session_stop", "session": s})
        print(f"⏹  已停止 {s}")
    return 0


def status(args) -> int:
    sessions = session_paths()
    if not sessions:
        print("目前没有远端 session 在跑。")
    for s, path in sessions.items():
        name, _, level = parse_session(s)
        print(f"● {name}　{level}　{path or '?'}")
        print(f"    接上去看：{attach_hint(s)}")
        stop_by = f"--dir {shlex.quote(path)}" if path else f"--name {name}"
        print(f"    停止：scripts/start-remote.sh --stop {stop_by}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="启动 / 停止远端 session")
    ap.add_argument("--level", choices=LEVELS, help="安全等级：readonly（只读）或 normal")
    ap.add_argument("--dir", help="工作资料夹（必须在 config.json 的 work_dirs 白名单里）")
    ap.add_argument("--name", help="手机上看到的名称（预设是资料夹名称）")
    ap.add_argument("--boot", action="store_true", help="开机服务用：只用 config.json 的预设值")
    ap.add_argument("--dry-run", action="store_true", help="只显示会做什么")
    ap.add_argument("--status", action="store_true", help="列出正在跑的远端 session")
    ap.add_argument("--stop", action="store_true", help="停止远端 session")
    ap.add_argument("--all", action="store_true", help="和 --stop 一起用：全部停止")
    ap.add_argument("--run", action="store_true", help=argparse.SUPPRESS)  # tmux 内部用
    args = ap.parse_args(argv)
    try:
        if args.run:
            return run_loop(args)
        if args.status:
            return status(args)
        if args.stop:
            return stop(args)
        return start(args)
    except (StartError, gitsnap.GitError) as e:
        print(f"❌ {e}")
        if args.boot or args.run:
            audit.write(pu.log_dir(), {"event": "session_start_failed", "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
