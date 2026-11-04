#!/bin/zsh
# Builds dist/Stenny.mcpb, the Claude Desktop extension: double-click it (or drag it onto Claude's Settings) to
# install. It only starts the `stenny mcp` already installed on the computer; see server/index.js.
# Needs Node.js (npx) for Anthropic's mcpb packer and Swift (Command Line Tools) for the icon.
set -euo pipefail
cd "${0:A:h}/.."

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bundle/server" dist
cp connector/server/index.js "$WORK/bundle/server/"
# the manifest with the package's version, so the extension and Stenny never disagree
sed "s/\"version\": \"[^\"]*\"/\"version\": \"$VERSION\"/" connector/manifest.json > "$WORK/bundle/manifest.json"
# the app icon without the transparent margin of Apple's icon grid (824 of 1024 px), so it fills Claude's tile
swift app/make-icon.swift "$WORK/icon.iconset" >/dev/null
sips -c 824 824 "$WORK/icon.iconset/icon_512x512@2x.png" --out "$WORK/crop.png" >/dev/null
sips -Z 512 "$WORK/crop.png" --out "$WORK/bundle/icon.png" >/dev/null

npx --yes @anthropic-ai/mcpb@2 validate "$WORK/bundle/manifest.json"
npx --yes @anthropic-ai/mcpb@2 pack "$WORK/bundle" dist/Stenny.mcpb
echo "Built dist/Stenny.mcpb (Stenny $VERSION)"
