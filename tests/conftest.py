"""所有测试共用的设定。"""

import pytest


@pytest.fixture(autouse=True)
def isolated_log_dir(tmp_path, monkeypatch):
    """测试产生的动作纪录写到暂时资料夹，不要混进真正的 audit log。

    子程序（hook）会继承这个环境变量，所以 subprocess 跑的 hook 也一样。
    """
    monkeypatch.setenv("REMOTE_CTRL_LOG_DIR", str(tmp_path / "test-audit-logs"))
