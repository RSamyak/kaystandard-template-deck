#!/usr/bin/env bash
# Render slides.qmd. Quarto and Deno caches stay in dump/.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p dump/home dump/deno dump/cache

export HOME="$ROOT/dump/home"
export DENO_DIR="$ROOT/dump/deno"
export XDG_CACHE_HOME="$ROOT/dump/cache"

quarto render slides.qmd
