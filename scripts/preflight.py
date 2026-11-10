#!/usr/bin/env python3
"""启动远端 session 前的检查清单。程式本体在 preflight/ 资料夹，阅读顺序见 preflight/__init__.py。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from preflight.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
