"""和 Claude 有关的检查。每个函数只看传进来的资料，不执行指令，所以容易测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import platform_utils as pu
from preflight.models import FAIL, OK, WARN, Check

EXPIRY_WARN_DAYS = 7


def check_platform(platform: str) -> Check:
    if platform == "darwin" or platform.startswith("linux"):
        return Check("平台", OK, pu.platform_name())
    return Check("平台", FAIL, f"不支持的平台：{platform}", "只支持 Ubuntu 和 macOS")


def check_claude_found(path: str | None) -> Check:
    if path:
        return Check("Claude Code 已安装", OK, path)
    return Check("Claude Code 已安装", FAIL, "找不到 claude", "安装 Claude Code：https://claude.com/claude-code")


def check_remote_flag(help_text: str, version: str, help_ok: bool = True) -> Check:
    """看 claude --help 里有没有 --remote-control（看功能，不看版本号）。"""
    name = "支持 Remote Control"
    if not help_ok:
        return Check(name, FAIL, "执行 claude --help 失败",
                     "手动跑 claude --help 看错误讯息；可能要重新安装 Claude Code")
    if "--remote-control" in help_text:
        return Check(name, OK, f"版本 {version}")
    return Check(name, FAIL, f"版本 {version} 没有 --remote-control", "执行 claude update 后重跑")


def check_auth(status: dict | None) -> Check:
    """必须是 claude.ai 订阅登录，不能是 API key / Bedrock / Vertex。"""
    name = "订阅登录（claude.ai）"
    if status is None:
        return Check(name, FAIL, "读不到 claude auth status", "执行 claude auth login")
    if not status.get("loggedIn"):
        return Check(name, FAIL, "未登录", "执行 claude auth login")
    method, provider = status.get("authMethod"), status.get("apiProvider")
    if method != "claude.ai" or provider != "firstParty":
        return Check(name, FAIL, f"登录方式是 {method} / {provider}",
                     "Remote Control 要用 claude.ai 订阅登录：claude auth login")
    plan = status.get("subscriptionType") or status.get("planTier") or "?"
    return Check(name, OK, f"方案 {plan}")


def check_login_expiry(status: dict | None, now: datetime) -> Check:
    """已过期 ❌；剩不到 7 天 ⚠️。"""
    name = "登录有效期"
    raw = (status or {}).get("loginExpiresAt")
    if not raw:
        return Check(name, WARN, "看不到到期时间")
    if not isinstance(raw, str):
        return Check(name, WARN, f"看不懂到期时间：{raw!r}")
    try:
        expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return Check(name, WARN, f"看不懂到期时间：{raw}")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)  # 没写时区就当 UTC
    left = expires - now
    day = expires.date().isoformat()
    if left <= timedelta(0):
        return Check(name, FAIL, f"已于 {day} 过期", "执行 claude auth login")
    if left < timedelta(days=EXPIRY_WARN_DAYS):
        return Check(name, WARN, f"{day} 到期，剩 {left.days} 天", "出门前重新登录：claude auth login")
    return Check(name, OK, f"{day} 到期，剩 {left.days} 天")


def check_trusted_account(status: dict | None, trusted_org: str | None) -> Check:
    """只信任你的手机 + 电脑：目前登录的 orgId 必须等于设定档里记录的。"""
    name = "可信账号（只信任你的手机 + 电脑）"
    current = (status or {}).get("orgId")
    if not trusted_org:
        return Check(name, FAIL, "这台电脑还没设定可信账号",
                     "在已确认的电脑跑 --show-org，再在每台电脑跑 --trust <orgId>")
    if not current:
        return Check(name, FAIL, "读不到目前登录的 orgId", "执行 claude auth login")
    if current != trusted_org:
        return Check(name, FAIL, "目前登录的账号不是可信账号",
                     "确认这台电脑登录的是你自己的账号；不要随便改 --trust")
    return Check(name, OK, f"orgId {current[:8]}… 一致")


def check_api_key_env(env: dict[str, str]) -> Check:
    """只看变量在不在，不读内容。启动远端 session 时这些变量会被移除。"""
    name = "会盖掉订阅登录的环境变量"
    found = [v for v in pu.OVERRIDING_AUTH_VARS if env.get(v)]
    if found:
        return Check(name, WARN, f"侦测到 {', '.join(found)}（不读取内容）",
                     "启动远端 session 时会自动移除，改用订阅登录；不影响其他项目")
    return Check(name, OK, "没有")
