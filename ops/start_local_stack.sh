#!/bin/zsh
set -euo pipefail

cd /Users/sid/Documents/Sales
/opt/homebrew/bin/colima start

for _ in {1..30}; do
  /usr/local/bin/docker info >/dev/null 2>&1 && break
  /opt/homebrew/bin/docker info >/dev/null 2>&1 && break
  sleep 2
done

/opt/homebrew/bin/docker-compose -f docker-compose.local.yml up -d
