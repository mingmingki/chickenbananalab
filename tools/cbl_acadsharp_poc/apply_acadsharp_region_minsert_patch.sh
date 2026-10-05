#!/bin/sh
set -eu

fork_root=${1:?usage: $0 /path/to/ACadSharp-v3.6.51}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# REGION/MINSERT first, then \U+XXXX for characters outside the code page,
# then XRECORD/extended-data string code pages and byte lengths, then TEXT
# with height 0, then SORTENTSTABLE dictionary keys, then reading strings our
# writer saved with character-count lengths before 2026-10-01, then the DXF
# code page name ANSI_949 (Korean AutoCAD DXF files), then pre-2007 strings
# stored as UTF-8 under the KS C 5601 code page, then R2013+ ACIS data (SAB)
# written as version 2 of the AC1018 entity, then the DWG writer lookups (block
# INSERTs and class instance counts made once per write, not per block/class),
# then the AC1018 MLEADER layout (R2007- arrowhead count, has-contents-block bit).
for patch_file in "$here/acadsharp-region-minsert.patch" "$here/acadsharp-unicode-escape.patch" "$here/acadsharp-xrecord-text.patch" "$here/acadsharp-text-height-zero.patch" "$here/acadsharp-sortents-key.patch" "$here/acadsharp-legacy-text-lengths.patch" "$here/acadsharp-dxf-codepage-ansi949.patch" "$here/acadsharp-misdeclared-utf8.patch" "$here/acadsharp-region-sab.patch" "$here/acadsharp-writer-lookups.patch" "$here/acadsharp-mleader-ac1018.patch"; do
  patch -d "$fork_root" -p1 --forward < "$patch_file"
done
printf '%s\n' "Applied ACadSharp REGION/MINSERT, unicode-escape, xrecord-text, text-height-zero, sortents-key, legacy-text-lengths, dxf-codepage-ansi949, misdeclared-utf8, region-sab, writer-lookups and mleader-ac1018 patches to $fork_root"
