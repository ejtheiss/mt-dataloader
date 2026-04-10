#!/usr/bin/env bash
#
# Local code health audit (read-only). Does not modify the repo.
#
# Prerequisites (install once in your venv):
#   pip install radon vulture pylint
#
# Usage (from repo root):
#   ./scripts/code_health_audit.sh
#   ./scripts/code_health_audit.sh --top 40 --min-dup-lines 6
#   ./scripts/code_health_audit.sh --skip-pylint --skip-vulture
#   ./scripts/code_health_audit.sh --out-dir reports/code_health
#
# --- Profiling playbook (CPU / memory) ---
# Flamegraph (sampling, low overhead; good for "where did CPU go?"):
#   py-spy record -o reports/code_health/profile.svg --subprocesses -- python -m pytest tests/ -q -k 'slowtest'
#   open reports/code_health/profile.svg
#
# cProfile + interactive view:
#   python -m cProfile -o reports/code_health/out.prof -m pytest tests/ -q
#   pip install snakeviz && snakeviz reports/code_health/out.prof
#   # or upload out.prof / convert to speedscope JSON for https://www.speedscope.app
#
# Heap / allocations (when memory is the suspect):
#   pip install memray
#   memray run -o reports/code_health/mem.bin -m pytest tests/ -q
#   memray flamegraph reports/code_health/mem.bin
#
# --- Optional dependency / "god module" hints ---
#   pip install pydeps
#   pydeps dataloader --max-bacon=2 -T svg -o reports/code_health/deps.svg
#
# --- Optional Ruff simplification pass (review diffs; widens rules vs CI) ---
#   ruff check dataloader flow_compiler --select SIM,UP,B --fix
#
# --- Overlapping duty (manual checklist) ---
# - Same JSON error shape built in multiple routers or handlers.
# - Two validation paths for the same user-facing "setup" or "flow" action.
# - Parallel copies of "fetch + merge + respond" that could share one helper.
# - Keep using: lint-imports (see pyproject.toml [tool.importlinter]) locally / in CI.
#
set -u

TOP=25
MIN_DUP_LINES=7
SKIP_PYLINT=0
SKIP_VULTURE=0
OUT_DIR=""

usage() {
  cat <<'EOF'
Usage: ./scripts/code_health_audit.sh [options]
  --top N              Top N files / complexity blocks (default: 25)
  --min-dup-lines N    pylint duplicate-code threshold (default: 7)
  --skip-pylint        Skip duplicate-code scan
  --skip-vulture       Skip unused-code scan
  --out-dir DIR        Also append all sections to DIR/audit-latest.txt
See script header for profiling (py-spy, cProfile, memray) and Ruff SIM,UP,B.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --top)
      TOP="${2:?}"
      shift 2
      ;;
    --min-dup-lines)
      MIN_DUP_LINES="${2:?}"
      shift 2
      ;;
    --skip-pylint)
      SKIP_PYLINT=1
      shift
      ;;
    --skip-vulture)
      SKIP_VULTURE=1
      shift
      ;;
    --out-dir)
      OUT_DIR="${2:?}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

PKGS=(dataloader flow_compiler db org models)

have() { command -v "$1" >/dev/null 2>&1; }

if ! have radon || ! have python3; then
  echo "ERROR: need radon and python3 on PATH (pip install radon)." >&2
  exit 1
fi

if [[ $SKIP_VULTURE -eq 0 ]] && ! have vulture; then
  echo "WARN: vulture not found; install with pip install vulture or use --skip-vulture." >&2
  SKIP_VULTURE=1
fi

if [[ $SKIP_PYLINT -eq 0 ]] && ! have pylint; then
  echo "WARN: pylint not found; install with pip install pylint or use --skip-pylint." >&2
  SKIP_PYLINT=1
fi

log_and_print() {
  local title="$1"
  shift
  if [[ -n "$OUT_DIR" ]]; then
    mkdir -p "$OUT_DIR"
    {
      echo ""
      echo "======== ${title} ========"
      echo ""
      "$@"
      echo ""
    } | tee -a "${OUT_DIR}/audit-latest.txt"
  else
    echo ""
    echo "======== ${title} ========"
    echo ""
    "$@"
    echo ""
  fi
}

if [[ -n "$OUT_DIR" ]]; then
  mkdir -p "$OUT_DIR"
  : >"${OUT_DIR}/audit-latest.txt"
fi

run_top_loc() {
  find "${PKGS[@]}" -name '*.py' -type f -print0 \
    ! -path '*/__pycache__/*' ! -path '*/.venv/*' ! -path '*/venv/*' 2>/dev/null \
    | while IFS= read -r -d '' f; do
        n=$(wc -l <"$f" | tr -d ' ')
        printf '%s\t%s\n' "$n" "$f"
      done | sort -rn | head -n "$TOP"
}

run_radon_top() {
  radon cc "${PKGS[@]}" -j -nc 2>/dev/null | python3 -c "
import json, sys
top = int('${TOP}')
data = json.load(sys.stdin)
items = []
for path, blocks in data.items():
    for b in blocks:
        items.append((b['complexity'], path, b['lineno'], b['type'], b['name']))
items.sort(key=lambda x: -x[0])
for c, path, ln, typ, name in items[:top]:
    print(f'{c:3d}  {path}:{ln}  {typ} {name}')
if not items:
    print('(no blocks reported)')
"
}

run_vulture_scan() {
  vulture "${PKGS[@]}" scripts/vulture_allowlist.py \
    --exclude .venv --exclude venv --exclude __pycache__ --exclude tests \
    --min-confidence 80 2>/dev/null || true
}

run_pylint_dup() {
  pylint "${PKGS[@]}" --disable=all --enable=duplicate-code \
    --min-similarity-lines="$MIN_DUP_LINES" 2>/dev/null || true
}

# --- 1) Largest Python files by line count (application packages only) ---
log_and_print "Top ${TOP} Python files by line count (LoC)" run_top_loc

# --- 2) Cyclomatic complexity leaders (radon JSON, all blocks merged) ---
log_and_print "Top ${TOP} blocks by cyclomatic complexity (radon cc)" run_radon_top

# --- 3) Likely dead code (triaged with scripts/vulture_allowlist.py) ---
if [[ $SKIP_VULTURE -eq 0 ]]; then
  log_and_print "Vulture (unused code; expect false positives — use vulture_allowlist.py)" run_vulture_scan
fi

# --- 4) Text-level duplicate blocks ---
if [[ $SKIP_PYLINT -eq 0 ]]; then
  log_and_print "Pylint duplicate-code (min similar lines = ${MIN_DUP_LINES})" run_pylint_dup
fi

echo ""
echo "Done. (Exit 0 — informational.)"
if [[ -n "$OUT_DIR" ]]; then
  echo "Wrote combined log to ${OUT_DIR}/audit-latest.txt"
fi
exit 0
