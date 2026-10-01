#!/bin/sh
set -eu

fork_root=${1:?usage: $0 /path/to/ACadSharp-v3.6.51}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# REGION/MINSERT first, then \U+XXXX for characters outside the code page,
# then XRECORD/extended-data string code pages and byte lengths.
for patch_file in "$here/acadsharp-region-minsert.patch" "$here/acadsharp-unicode-escape.patch" "$here/acadsharp-xrecord-text.patch"; do
  patch -d "$fork_root" -p1 --forward < "$patch_file"
done
printf '%s\n' "Applied ACadSharp REGION/MINSERT, unicode-escape and xrecord-text patches to $fork_root"
