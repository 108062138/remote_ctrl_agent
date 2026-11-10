#!/bin/sh
# 简短入口：scripts/install.sh（安装）、scripts/install.sh --uninstall（移除）
exec python3 "$(dirname "$0")/install_service.py" "$@"
