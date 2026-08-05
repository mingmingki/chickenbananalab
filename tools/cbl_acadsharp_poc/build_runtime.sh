#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
dotnet_bin=${DOTNET_BIN:-$(command -v dotnet || true)}
acadsharp_root=${ACADSHARP_SOURCE_ROOT:-}
cli_home=${DOTNET_CLI_HOME:-$project_root/.dotnet-cli-home}
runtime_dir=$project_root/tools/cbl_acadsharp_poc/runtime

if [ -z "$dotnet_bin" ] || [ ! -x "$dotnet_bin" ]; then
  echo "dotnet SDK not found; set DOTNET_BIN to an official SDK executable" >&2
  exit 2
fi
if [ -z "$acadsharp_root" ] || [ ! -f "$acadsharp_root/src/ACadSharp/ACadSharp.csproj" ]; then
  echo "ACadSharp source not found: $acadsharp_root" >&2
  exit 2
fi

mkdir -p "$cli_home"
project="$project_root/tools/cbl_acadsharp_poc/CblAcadSharpPoc.csproj"
for rid in osx-arm64 linux-x64; do
  publish_dir="$project_root/tools/cbl_acadsharp_poc/bin/Release/net9.0/$rid/publish"
  target_dir="$runtime_dir/macos-arm64"
  if [ "$rid" = "linux-x64" ]; then target_dir="$runtime_dir/linux-x64"; fi
  mkdir -p "$target_dir"
  DOTNET_CLI_HOME="$cli_home" "$dotnet_bin" restore "$project" -r "$rid" \
    -p:TargetFramework=net9.0 -p:ACadSharpSourceRoot="$acadsharp_root" --nologo
  DOTNET_CLI_HOME="$cli_home" "$dotnet_bin" publish "$project" -f net9.0 \
    -c Release -r "$rid" --self-contained true -p:PublishSingleFile=true \
    -p:IncludeNativeLibrariesForSelfExtract=true -p:PublishTrimmed=false \
    -p:LangVersion=preview -p:TargetFramework=net9.0 \
    -p:ACadSharpSourceRoot="$acadsharp_root" --nologo --no-restore \
    -p:PublishDir="$publish_dir/"
  cp "$publish_dir/CblAcadSharpPoc" "$target_dir/CblAcadSharpPoc.bin"
  chmod +x "$target_dir/CblAcadSharpPoc.bin"
done

mkdir -p "$runtime_dir"
cp "$project_root/tools/cbl_acadsharp_poc/runtime_launcher.sh" "$runtime_dir/CblAcadSharpPoc"
chmod +x "$runtime_dir/CblAcadSharpPoc"

test -x "$runtime_dir/CblAcadSharpPoc"
test -x "$runtime_dir/macos-arm64/CblAcadSharpPoc.bin"
test -x "$runtime_dir/linux-x64/CblAcadSharpPoc.bin"
printf '%s\n' "$runtime_dir/CblAcadSharpPoc"
