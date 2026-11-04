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
swift app/make-icon.swift "$WORK/icon.iconset" >/dev/null   # the app icon, at 512 px
cp "$WORK/icon.iconset/icon_256x256@2x.png" "$WORK/bundle/icon.png"

npx --yes @anthropic-ai/mcpb@2 validate "$WORK/bundle/manifest.json"
npx --yes @anthropic-ai/mcpb@2 pack "$WORK/bundle" dist/Stenny.mcpb
echo "Built dist/Stenny.mcpb (Stenny $VERSION)"
