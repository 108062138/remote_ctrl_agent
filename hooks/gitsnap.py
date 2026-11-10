"""git 还原点（checkpoint）：把整个工作目录拍一张快照，存成 git 的参照，不动到你的档案。

怎么做到「不动档案」：
  1. 用一个暂时的 index（不是 repo 本来的），把工作目录所有档案加进去（含未追踪、排除 .gitignore）
  2. git write-tree + commit-tree 做出一个 commit（不在任何分支上）
  3. git update-ref 存到 refs/remote-ctrl/checkpoints/<UTC 时间>
  工作目录、repo 的 index、目前分支、HEAD 都不会变。

还原（restore）时会先把「现在」也存一个还原点（包含会被盖掉的 .gitignore 档案），所以还原本身也可以反悔。

时间限制：所有 git 指令共用一个期限（deadline），超过就丢 GitError，
让 hook 有时间说明「这次没有还原点」，而不是被 Claude Code 中途砍掉。
只用标准库 + git 指令。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REF_PREFIX = "refs/remote-ctrl/checkpoints/"
KEEP = 50                 # 最多保留几个还原点，旧的自动删掉
GIT_TIMEOUT = 60          # 单一 git 指令最多几秒
IDENTITY = {
    "GIT_AUTHOR_NAME": "remote-ctrl", "GIT_AUTHOR_EMAIL": "remote-ctrl@localhost",
    "GIT_COMMITTER_NAME": "remote-ctrl", "GIT_COMMITTER_EMAIL": "remote-ctrl@localhost",
}


class GitError(Exception):
    pass


@dataclass
class Checkpoint:
    ref: str        # refs/remote-ctrl/checkpoints/20261110T103000.123456Z
    commit: str     # commit hash
    created: str    # 当地时间，给人看：2026-11-10 18:30:00
    label: str      # 拍快照的原因（例如手机下的 prompt 开头）

    @property
    def id(self) -> str:
        return self.ref[len(REF_PREFIX):]


def _git(repo: Path, *args: str, env: dict | None = None, check: bool = True,
         deadline: float | None = None) -> str:
    """执行 git。输出用 surrogateescape 解码：怪异编码的档名也能原样拿回来用。"""
    timeout = GIT_TIMEOUT
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise GitError("超过时间限制")
    full_env = {**os.environ, **IDENTITY, **(env or {})}
    try:
        p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
                           capture_output=True, env=full_env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} 超过时间限制") from None
    except OSError as e:
        raise GitError(f"无法执行 git：{e}") from None
    out = p.stdout.decode("utf-8", "surrogateescape")
    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise GitError(err or f"git {' '.join(args)} 失败")
    return out


def repo_root(path: Path) -> Path | None:
    """path 所在的 git repo 根目录；不是 git repo 时回传 None。"""
    try:
        out = _git(Path(path), "rev-parse", "--show-toplevel")
    except GitError:
        return None
    return Path(out.strip())


def _need_repo(path: Path) -> Path:
    repo = repo_root(path)
    if repo is None:
        raise GitError(f"不是 git repo：{path}")
    return repo


def _head(repo: Path, deadline=None) -> str | None:
    out = _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False, deadline=deadline).strip()
    return out or None


def snapshot_tree(path: Path, force: list[str] = (), deadline: float | None = None) -> str:
    """用暂时的 index 把整个工作目录做成 tree，回传 tree hash。

    force：额外强制加入的档案（.gitignore 排除、但还原时会被盖掉的档案）。
    """
    repo = _need_repo(path)
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir", deadline=deadline).strip())
    with tempfile.TemporaryDirectory(prefix="remote-ctrl-") as tmp:
        index = Path(tmp) / "index"
        real_index = git_dir / "index"
        if real_index.exists():
            shutil.copyfile(real_index, index)  # 从现有 index 开始，比较快
        env = {"GIT_INDEX_FILE": str(index)}
        _git(repo, "add", "-A", "--", ".", env=env, deadline=deadline)
        if force:
            _git(repo, "add", "-f", "--", *force, env=env, deadline=deadline)
        return _git(repo, "write-tree", env=env, deadline=deadline).strip()


def create(path: Path, label: str = "", *, tree: str | None = None, protect: tuple = (),
           deadline: float | None = None) -> Checkpoint:
    """拍一个还原点。tree 已经算好时直接用（避免重复扫描整个工作目录）。"""
    repo = _need_repo(path)
    if tree is None:
        tree = snapshot_tree(repo, deadline=deadline)
    now = datetime.now(timezone.utc)
    local = now.astimezone()
    message = f"remote-ctrl checkpoint {local:%Y-%m-%d %H:%M:%S}\n\n{label.strip()[:500]}"
    head = _head(repo, deadline)
    parents = ["-p", head] if head else []
    commit = _git(repo, "commit-tree", tree, *parents, "-m", message, deadline=deadline).strip()
    ref = f"{REF_PREFIX}{now:%Y%m%dT%H%M%S.%f}Z"   # UTC：不受日光节约时间、时区影响，排序正确
    _git(repo, "update-ref", ref, commit, deadline=deadline)
    prune(repo, protect=(ref, *protect), deadline=deadline)
    return Checkpoint(ref, commit, f"{local:%Y-%m-%d %H:%M:%S}", label.strip()[:500])


def list_checkpoints(path: Path, deadline: float | None = None) -> list[Checkpoint]:
    """新的在前。"""
    repo = _need_repo(path)
    out = _git(repo, "for-each-ref", "--sort=-refname",
               "--format=%(refname)%00%(objectname)%00%(contents)%00%00", REF_PREFIX, deadline=deadline)
    result = []
    for record in out.split("\0\0"):
        record = record.strip("\n")
        if not record:
            continue
        ref, commit, message = (record.split("\0") + ["", ""])[:3]
        first, _, rest = message.partition("\n")
        created = first.replace("remote-ctrl checkpoint ", "").strip()
        result.append(Checkpoint(ref, commit, created, rest.strip()))
    return result


def find(path: Path, cid: str) -> Checkpoint:
    """用 id（或开头几个字）找还原点。"""
    matches = [c for c in list_checkpoints(path) if c.id.startswith(cid) or c.commit.startswith(cid)]
    if not matches:
        raise GitError(f"找不到还原点：{cid}")
    if len(matches) > 1:
        raise GitError(f"有 {len(matches)} 个还原点符合 {cid}，请多打几个字")
    return matches[0]


def prune(repo: Path, keep: int | None = None, protect: tuple = (), deadline: float | None = None) -> int:
    """只留最新的 keep 个（预设 KEEP）；protect 里的（例如正要还原的目标）绝对不删。"""
    keep = KEEP if keep is None else keep
    old = [c for c in list_checkpoints(repo, deadline)[keep:] if c.ref not in protect]
    for c in old:
        _git(repo, "update-ref", "-d", c.ref, deadline=deadline)
    return len(old)


def _files_in(repo: Path, treeish: str) -> set[str]:
    out = _git(repo, "ls-tree", "-r", "--name-only", "-z", treeish)
    return {f for f in out.split("\0") if f}


def _ignored_files(repo: Path) -> set[str]:
    """目前存在、但被 .gitignore 排除的档案。"""
    out = _git(repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    return {f for f in out.split("\0") if f}


def changes_since(path: Path, cp: Checkpoint, tree: str | None = None) -> dict[str, list[str]]:
    """和还原点比，现在的工作目录：多了哪些、少了哪些、改了哪些档案。"""
    repo = _need_repo(path)
    tree = tree or snapshot_tree(repo)
    out = _git(repo, "diff", "--name-status", "--no-renames", "-z", cp.commit, tree)
    parts = [p for p in out.split("\0") if p]
    result = {"added": [], "deleted": [], "modified": []}
    for status, name in zip(parts[::2], parts[1::2]):
        key = {"A": "added", "D": "deleted"}.get(status[0], "modified")
        result[key].append(name)
    return result


def diff_text(path: Path, cp: Checkpoint, tree: str | None = None) -> str:
    repo = _need_repo(path)
    tree = tree or snapshot_tree(repo)
    return _git(repo, "diff", "--no-renames", cp.commit, tree)


def restore(path: Path, cp: Checkpoint, tree: str | None = None) -> Checkpoint:
    """把工作目录还原成还原点的样子。先把现在存成一个新还原点，回传它（反悔用）。

    - 还原点里有的档案：内容还原
    - 还原点之后才新增的档案：删除（已经存在「还原前」的还原点里）
    - .gitignore 排除的档案：不删；但若还原点里有同名档案会被盖掉，所以先强制存进「还原前」
    - repo 的 index 会回到 HEAD（还原后的修改都是「未暂存」）
    tree：现在工作目录的快照（已经算好时传进来，避免重复扫描）
    """
    repo = _need_repo(path)
    target_files = _files_in(repo, cp.commit)
    overwritten_ignored = sorted(target_files & _ignored_files(repo))
    if overwritten_ignored or tree is None:
        tree = snapshot_tree(repo, force=overwritten_ignored)
    before = create(repo, f"还原前自动存档（准备还原到 {cp.id}）", tree=tree, protect=(cp.ref,))
    current_files = _files_in(repo, before.commit)
    for name in sorted(current_files - target_files):
        f = repo / name
        if f.is_file() or f.is_symlink():
            f.unlink()
    if target_files:
        _git(repo, "checkout", cp.commit, "--", ".")
    if _head(repo):
        _git(repo, "reset", "-q")
    else:
        _git(repo, "rm", "-r", "-q", "--cached", "--ignore-unmatch", ".")
    return before
