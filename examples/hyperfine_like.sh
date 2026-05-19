#!/usr/bin/env bash
# Hyperfine-style command-line usage: no Python script, just `benchr bench`.
set -euo pipefail
exec benchr bench --warmup 2 --runs 10 'sleep 0.05' 'sleep 0.10' "$@"
