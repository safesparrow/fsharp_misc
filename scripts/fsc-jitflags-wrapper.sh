#!/bin/bash
# Stand-in for the `dotnet` host used by the Fsc MSBuild task: applies the JIT flags to the fsc process only.
# The Fsc task runs: $(FscToolPath)/$(FscToolExe) "<fsc.dll>" @<rsp>
export DOTNET_TieredPGO=0 DOTNET_TC_CallCountingDelayMs=0
exec "${FSC_JITFLAGS_DOTNET:?FSC_JITFLAGS_DOTNET not set}" "$@"
