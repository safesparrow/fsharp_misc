#!/bin/bash
# FscToolExe wrapper for whole-build benchmarks (buildbench3.py): applies a config's runtime settings to the fsc
# process only. Every FSCWRAP_<NAME>=<value> in the environment is exported as <NAME>=<value>, then
# $FSC_WRAP_DOTNET runs the compiler with the original arguments ("<fsc.dll>" @rsp).
for v in $(compgen -e); do
  case $v in FSCWRAP_*) export "${v#FSCWRAP_}=${!v}" ;; esac
done
[[ -n "${FSC_WRAP_LOG:-}" ]] && echo "$(date +%s) $*" >> "$FSC_WRAP_LOG"
exec "${FSC_WRAP_DOTNET}" "$@"
