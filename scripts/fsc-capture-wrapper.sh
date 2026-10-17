#!/bin/bash
# Stand-in for the `dotnet` host used by the Fsc MSBuild task.
# The Fsc task runs: $(FscToolPath)/$(FscToolExe) "<fsc.dll>" @<temp.rsp>
# We record cwd, argv, the response file and the DOTNET_* env, then exec the real host.
# Required env: FSC_CAPTURE_DIR (output dir), FSC_CAPTURE_DOTNET (real dotnet host).
set -euo pipefail
dir="${FSC_CAPTURE_DIR:?FSC_CAPTURE_DIR not set}"
mkdir -p "$dir"
id="$(date +%s)-$$-$RANDOM"
pwd > "$dir/$id.cwd"
printf '%s\n' "$@" > "$dir/$id.argv"
for a in "$@"; do
  case "$a" in
    @*) cp "${a#@}" "$dir/$id.rsp" ;;
  esac
done
(env | grep -E '^(DOTNET_|COMPlus_)' || true) > "$dir/$id.env"
exec "${FSC_CAPTURE_DOTNET:?FSC_CAPTURE_DOTNET not set}" "$@"
