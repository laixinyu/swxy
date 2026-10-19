#!/bin/bash
set -e

# 迁移失败就不启动，避免代码和表结构不一致时带病运行
python -m db.migrate

exec "$@"
