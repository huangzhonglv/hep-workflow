#!/bin/zsh

set -euo pipefail

usage() {
  print -u2 -- "Usage: $0 RESULT_DIR [--blocked]"
  print -u2 -- "   or: $0 --task-dir TASK_DIR [--blocked]"
}

blocked=0
batch_mode=0
result_dir=""
task_dir=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --blocked)
      blocked=1
      shift
      ;;
    --task-dir)
      if [[ -n "$result_dir" || -n "$task_dir" ]]; then
        usage
        exit 1
      fi
      batch_mode=1
      shift
      if [[ $# -eq 0 ]]; then
        usage
        exit 1
      fi
      task_dir="$1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ $batch_mode -eq 1 || -n "$result_dir" ]]; then
        usage
        exit 1
      fi
      result_dir="$1"
      shift
      ;;
  esac
done

if [[ $batch_mode -eq 1 ]]; then
  if [[ -z "$task_dir" ]]; then
    usage
    exit 1
  fi
  result_dir="$task_dir"
elif [[ -z "$result_dir" ]]; then
  usage
  exit 1
fi

result_dir=${result_dir:A}
script_dir=${0:A:h}
skill_dir=${script_dir:h}
template_dir="$skill_dir/templates"

if [[ ! -d "$template_dir" ]]; then
  print -u2 -- "template directory not found: $template_dir"
  exit 1
fi

mkdir -p "$result_dir"

generated_at=$(date '+%Y-%m-%d %H:%M:%S %z')
result_wl_path="$result_dir/result.wl"
wolframscript_bin="$(command -v wolframscript || true)"
if [[ -z "$wolframscript_bin" ]]; then
  wolframscript_bin="/Applications/Wolfram.app/Contents/MacOS/wolframscript"
fi
run_command="$wolframscript_bin -file $result_wl_path"

escape_sed_replacement() {
  print -nr -- "$1" | sed -E 's#([&|/])#\\\1#g'
}

render_template() {
  local template_path="$1"
  local output_path="$2"
  local allow_overwrite="$3"
  local rendered
  local generated_at_escaped
  local result_dir_escaped
  local result_wl_path_escaped
  local run_command_escaped

  if [[ -e "$output_path" && "$allow_overwrite" -eq 0 ]]; then
    print -u2 -- "skip existing: $output_path"
    return 0
  fi

  generated_at_escaped=$(escape_sed_replacement "$generated_at")
  result_dir_escaped=$(escape_sed_replacement "$result_dir")
  result_wl_path_escaped=$(escape_sed_replacement "$result_wl_path")
  run_command_escaped=$(escape_sed_replacement "$run_command")

  rendered=$(sed \
    -e "s|{{GENERATED_AT}}|$generated_at_escaped|g" \
    -e "s|{{RESULT_DIR}}|$result_dir_escaped|g" \
    -e "s|{{RESULT_WL_PATH}}|$result_wl_path_escaped|g" \
    -e "s|{{RUN_COMMAND}}|$run_command_escaped|g" \
    "$template_path")

  print -r -- "$rendered" > "$output_path"
  print -r -- "$output_path"
}

copy_template_raw() {
  local template_path="$1"
  local output_path="$2"
  local allow_overwrite="$3"

  if [[ -e "$output_path" && "$allow_overwrite" -eq 0 ]]; then
    print -u2 -- "skip existing: $output_path"
    return 0
  fi

  cp "$template_path" "$output_path"
  print -r -- "$output_path"
}

overwrite_existing=$batch_mode

render_template "$template_dir/request.md.tmpl" "$result_dir/request.md" "$overwrite_existing"
render_template "$template_dir/result-summary.md.tmpl" "$result_dir/result-summary.md" "$overwrite_existing"

if [[ $blocked -eq 0 ]]; then
  render_template "$template_dir/run-instructions.md.tmpl" "$result_dir/run-instructions.md" "$overwrite_existing"
fi

if [[ $batch_mode -eq 1 && $blocked -eq 0 ]]; then
  copy_template_raw "$template_dir/result-python.py.tmpl" "$result_dir/result-python.py" "$overwrite_existing"
  copy_template_raw "$template_dir/result-meta.json.tmpl" "$result_dir/result-meta.json" "$overwrite_existing"
fi
