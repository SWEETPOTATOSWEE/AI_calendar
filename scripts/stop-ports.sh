#!/usr/bin/env bash
set -euo pipefail

for port in 3000 8000; do
  pids=$(ss -lptn "sport = :${port}" | sed -n 's/.*pid=\([0-9]\+\).*/\1/p')
  if [ -n "$pids" ]; then
    kill $pids
  fi
done
