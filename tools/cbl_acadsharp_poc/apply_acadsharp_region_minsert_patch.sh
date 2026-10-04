#!/bin/sh
set -eu

fork_root=${1:?usage: $0 /path/to/ACadSharp-v3.6.51}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# REGION/MINSERT first, then \U+XXXX for characters outside the code page,
# then XRECORD/extended-data string code pages and byte lengths, then TEXT
# with height 0, then SORTENTSTABLE dictionary keys, then reading strings our
# writer saved with character-count lengths before 2026-10-01, then the DXF
# code page name ANSI_949 (Korean AutoCAD DXF files).
for patch_file in "$here/acadsharp-region-minsert.patch" "$here/acadsharp-unicode-escape.patch" "$here/acadsharp-xrecord-text.patch" "$here/acadsharp-text-height-zero.patch" "$here/acadsharp-sortents-key.patch" "$here/acadsharp-legacy-text-lengths.patch" "$here/acadsharp-dxf-codepage-ansi949.patch"; do
  patch -d "$fork_root" -p1 --forward < "$patch_file"
done
printf '%s\n' "Applied ACadSharp REGION/MINSERT, unicode-escape, xrecord-text, text-height-zero, sortents-key, legacy-text-lengths and dxf-codepage-ansi949 patches to $fork_root"
