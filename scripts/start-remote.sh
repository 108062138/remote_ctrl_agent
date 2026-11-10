#!/bin/sh
# 简短入口：scripts/start-remote.sh --level normal --dir ~/Desktop/learn_git
exec python3 "$(dirname "$0")/start_remote.py" "$@"
