#!/bin/zsh

set -euo pipefail

usage() {
  print -u2 -- "Usage: $0 RESULT_DIR"
}

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

result_dir=${1:A}

if [[ ! -d "$result_dir" ]]; then
  print -u2 -- "result directory not found: $result_dir"
  exit 1
fi

files=()

for relpath in request.md result-summary.md run-instructions.md result-python.py result-meta.json; do
  file_path="$result_dir/$relpath"
  if [[ -f "$file_path" ]]; then
    files+=("$file_path")
  fi
done

if [[ ${#files[@]} -eq 0 ]]; then
  print -u2 -- "no managed result files found under: $result_dir"
  exit 1
fi

pattern='\{\{\s*[A-Za-z0-9_]+\s*\}\}'

if command -v rg >/dev/null 2>&1; then
  if rg -n "$pattern" "${files[@]}"; then
    print -u2 -- ""
    print -u2 -- "ERROR: unresolved template placeholders found in $result_dir"
    exit 1
  else
    scan_status=$?
    if [[ $scan_status -ne 1 ]]; then
      print -u2 -- "placeholder scan failed"
      exit 2
    fi
  fi
else
  if grep -nE "$pattern" "${files[@]}"; then
    print -u2 -- ""
    print -u2 -- "ERROR: unresolved template placeholders found in $result_dir"
    exit 1
  else
    scan_status=$?
    if [[ $scan_status -ne 1 ]]; then
      print -u2 -- "placeholder scan failed"
      exit 2
    fi
  fi
fi

print -r -- "OK: no unresolved template placeholders found in $result_dir"
