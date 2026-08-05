#!/bin/sh
set -eu

fork_root=${1:?usage: $0 /path/to/ACadSharp-v3.6.51}
patch_file=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/acadsharp-region-minsert.patch

patch -d "$fork_root" -p1 --forward < "$patch_file"
printf '%s\n' "Applied ACadSharp REGION/MINSERT POC patch to $fork_root"
