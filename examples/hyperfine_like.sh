#!/usr/bin/env bash
# Hyperfine-style command-line usage: no Python script, just `bench run`.
set -euo pipefail
uv run bench run --warmup 2 --runs 10 'sleep 0.05' 'sleep 0.10' "$@"
