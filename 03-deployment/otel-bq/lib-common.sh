#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Shared helpers. Sourced by otel-headers-helper.sh and print-settings.sh;
# not meant to be run directly.

# find_python — print the path of a Python interpreter that actually runs, or
# nothing. Order: an explicit CLOUDSDK_PYTHON, then python3/python from PATH,
# then the Cloud SDK's bundled interpreter.
#
# The PATH candidates are probed rather than trusted because on a stock Windows
# box `python`/`python3` resolve to the Microsoft Store alias stub, which prints
# "Python was not found" and exits non-zero. Anyone using this collector has
# gcloud, so its bundled interpreter is a dependable last resort.
find_python() {
  local cand
  for cand in "${CLOUDSDK_PYTHON:-}" python3 python; do
    [[ -n "${cand}" ]] || continue
    if command -v "${cand}" >/dev/null 2>&1 && "${cand}" -c 'import json,sys' >/dev/null 2>&1; then
      printf '%s' "${cand}"
      return 0
    fi
  done
  for cand in \
    "/c/Program Files (x86)/Google/Cloud SDK/google-cloud-sdk/platform/bundledpython/python.exe" \
    "/c/Program Files/Google/Cloud SDK/google-cloud-sdk/platform/bundledpython/python.exe" \
    "${HOME}/AppData/Local/Google/Cloud SDK/google-cloud-sdk/platform/bundledpython/python.exe"; do
    if [[ -x "${cand}" ]]; then
      printf '%s' "${cand}"
      return 0
    fi
  done
  return 1
}

# is_windows_shell — true under Git Bash / MSYS2 / Cygwin.
is_windows_shell() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

# ensure_cloudsdk_python — on Windows, make the POSIX `gcloud` wrapper usable.
#
# The Cloud SDK ships two launchers: gcloud.cmd (used from PowerShell/cmd, which
# points at the SDK's own bundled interpreter) and a POSIX `gcloud` shell script,
# found first on the Git Bash PATH, which looks for `python` on PATH — hitting
# the Store stub, so every gcloud call dies with "Python was not found".
ensure_cloudsdk_python() {
  is_windows_shell || return 0
  [[ -z "${CLOUDSDK_PYTHON:-}" ]] || return 0
  local py
  py="$(find_python)" || return 0
  export CLOUDSDK_PYTHON="${py}"
}
