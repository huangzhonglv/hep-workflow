#!/usr/bin/env bash

set -euo pipefail

input_dir="${1:-$PWD}"
base_dir="$(cd "$input_dir" && pwd -P)"

repo_root="$base_dir"
while [[ "$repo_root" != "/" ]]; do
  if [[ -d "$repo_root/workspace" ]]; then
    break
  fi
  repo_root="$(dirname "$repo_root")"
done

if [[ ! -d "$repo_root/workspace" ]]; then
  repo_root="$base_dir"
fi

results_root="$repo_root/workspace/package-scribe"

mkdir -p "$results_root"

max_index=0

shopt -s nullglob
dirs=("$results_root"/package-result[0-9][0-9][0-9])
shopt -u nullglob

if (( ${#dirs[@]} > 0 )); then
  for dir in "${dirs[@]}"; do
    name=${dir##*/}
    index=${name#package-result}
    # Use 10# so values like 008 are parsed as decimal, not octal.
    num=$((10#$index))
    if (( num > max_index )); then
      max_index=$num
    fi
  done
fi

next_index=$((max_index + 1))
next_name=$(printf 'package-result%03d' "$next_index")
next_dir="$results_root/$next_name"

mkdir -p "$next_dir"
printf '%s\n' "$next_dir"
