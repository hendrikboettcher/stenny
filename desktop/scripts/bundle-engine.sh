#!/usr/bin/env bash
# Stenny's engine for the app to carry inside (#92): a standalone Python (python-build-standalone, pinned and checked
# against its SHA-256) with the engine and its packages installed into it, at src-tauri/engine/. Tauri bundles that
# folder as a resource (tauri.conf.json): Stenny.app/Contents/Resources/engine on a Mac, engine\ next to Stenny.exe
# on Windows. The app runs it as `<engine>/bin/python3 -m stenny` (Windows `<engine>\python.exe -m stenny`), see
# src-tauri/src/engine.rs; nothing is frozen, so mlx (Metal) and CTranslate2 load their libraries as they always do.
#
# The packages are the ones in uv.lock (hashes checked), for this system: mlx-whisper on a Mac, faster-whisper on
# Windows (pyproject.toml's markers). The engine itself goes in as a wheel built from this checkout, so whatever is
# committed (or changed) here is what the app gets. The Whisper models stay downloads (paths.models_dir()).
#
# Every .pyc is compiled once here as "unchecked-hash" and the engine runs with PYTHONDONTWRITEBYTECODE: Python then
# never writes into the app, whose signature (scripts/install-mac.sh) would otherwise break.
#
# Runs on the system it builds for: macOS (Apple Silicon) or Windows in Git Bash (CI's Windows jobs). Needs uv.
# Downloads ~25 MB of Python plus the packages (uv's cache), the first time.
#   desktop/scripts/bundle-engine.sh [target triple, default: this computer's]
set -euo pipefail
cd "$(dirname "$0")/../src-tauri"
ROOT="$(cd ../.. && pwd)"

RELEASE=20261026
PYTHON=3.12.16
TRIPLE="${1:-$(rustc -vV 2>/dev/null | sed -n 's/^host: //p' || true)}"
if [[ -z "$TRIPLE" ]]; then   # no Rust here (CI's engine job): this computer's
    case "$(uname -s)-$(uname -m)" in
        Darwin-arm64) TRIPLE=aarch64-apple-darwin ;;
        MINGW*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64) TRIPLE=x86_64-pc-windows-msvc ;;
    esac
fi
case "$TRIPLE" in
    aarch64-apple-darwin)
        SHA256=353d5f1b4fd2715e7053f12f068435d5bbad16b91e057006b5d01e0925709a7e ;;
    x86_64-pc-windows-msvc)
        SHA256=1f2ab53880fc67ea323398c6388e05af28addc99a8e389209f08a51fdff4b149 ;;
    *) echo "❌ No standalone Python pinned for '$TRIPLE' (Apple Silicon Macs and 64-bit Windows only)."; exit 1 ;;
esac
FILE="cpython-$PYTHON+$RELEASE-$TRIPLE-install_only_stripped.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/$RELEASE/$FILE"
UV="$(command -v uv || ls "$HOME/.local/bin/uv" "$HOME/.local/bin/uv.exe" 2>/dev/null | head -1 || true)"
[[ -n "$UV" ]] || { echo "❌ uv isn't installed: https://docs.astral.sh/uv/"; exit 1; }

OUT=engine
if [[ "$TRIPLE" == *windows* ]]; then PY="$OUT/python.exe"; SITE="$OUT/Lib/site-packages"
else PY="$OUT/bin/python3"; SITE="$OUT/lib/python3.12/site-packages"; fi

# The download is kept (in the build folder, which Spotlight skips) so a rebuild works offline
CACHE=target.noindex/engine-download
mkdir -p "$CACHE"
if [[ ! -s "$CACHE/$FILE" ]]; then
    curl -fL --retry 3 -o "$CACHE/$FILE.part" "$URL"
    mv "$CACHE/$FILE.part" "$CACHE/$FILE"
fi
SUM="$( (sha256sum "$CACHE/$FILE" 2>/dev/null || shasum -a 256 "$CACHE/$FILE") | cut -d' ' -f1)"
[[ "$SUM" == "$SHA256" ]] || { rm -f "$CACHE/$FILE"; echo "❌ $FILE isn't the expected file (SHA-256 $SUM)."; exit 1; }

# relative paths only, from here on: Git Bash would have to translate /tmp/… for uv.exe and python.exe
WORK=target.noindex/engine-work
rm -rf "$WORK" && mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT
rm -rf "$OUT"
tar -xzf "$CACHE/$FILE" -C "$WORK"
mv "$WORK/python" "$OUT"

# What Stenny never uses: headers and import libraries (for building extensions), Tcl/Tk and IDLE, pip's bootstrap,
# the stdlib's tests, man pages, and bin/'s scripts (pip, idle, pydoc: their first line names the build machine).
if [[ "$TRIPLE" == *windows* ]]; then
    rm -rf "$OUT"/{include,libs,tcl,Scripts} "$OUT"/Lib/{tkinter,idlelib,turtledemo,ensurepip,test} \
           "$OUT"/DLLs/{_tkinter.pyd,tcl86t.dll,tk86t.dll}
else
    rm -rf "$OUT"/{include,share} "$OUT"/lib/{tcl*,tk*,itcl*,thread*,pkgconfig,libtcl*,libtk*} \
           "$OUT"/lib/python3.12/{tkinter,idlelib,turtledemo,ensurepip,test,lib-dynload/_tkinter*} \
           "$OUT"/lib/python3.12/config-3.12-darwin
    # one real program, bin/python3 (Tauri copies resources without their symbolic links)
    find "$OUT/bin" -mindepth 1 ! -name python3.12 -exec rm -f {} +
    mv "$OUT/bin/python3.12" "$OUT/bin/python3"
fi
# any other symbolic link becomes the file it points to (the app's resources can't carry links)
find "$OUT" -type l | while IFS= read -r link; do
    target="$(cd "$(dirname "$link")" && cd "$(dirname "$(readlink "$link")")" 2>/dev/null && pwd)/$(basename "$(readlink "$link")")"
    rm "$link"
    [[ -e "$target" ]] && cp -R "$target" "$link"
done

# The packages exactly as locked (hashes checked), then the engine from this checkout. --no-deps: uv.lock already
# lists everything (and leaves out torch, see pyproject.toml's exclude-dependencies).
"$UV" export -q --project ../.. --frozen --no-dev --no-emit-project --format requirements.txt -o "$WORK/requirements.txt"
"$UV" pip install -q --python "$PY" --no-deps --require-hashes --break-system-packages -r "$WORK/requirements.txt"
"$UV" build -q --wheel --out-dir "$WORK/wheel" ../..
"$UV" pip install -q --python "$PY" --no-deps --break-system-packages "$WORK"/wheel/stenny-*.whl
# The packages' commands (stenny, hf, f2py …) name this build folder in their first line (the app runs
# `python -m stenny` instead), pip came with the Python, mlx brings its C++ headers and CMake files: none of it runs
if [[ "$TRIPLE" == *windows* ]]; then rm -rf "$OUT/Scripts"
else find "$OUT/bin" -mindepth 1 ! -name python3 -exec rm -rf {} +; fi
rm -rf "$SITE"/pip "$SITE"/pip-*.dist-info \
       "$SITE"/mlx/{include,share} "$SITE"/mlx/lib/cmake

# What the engine reads from its checkout's top (config.py: `stenny init`, a first config), next to its code
cp "$ROOT/config.example.toml" "$SITE/stenny/config.example.toml"
# Packages in the user's own site-packages (`pip install --user` for another Python 3.12) must never shadow the app's
if [[ "$TRIPLE" == *windows* ]]; then SITE_PY="$OUT/Lib/site.py"; else SITE_PY="$OUT/lib/python3.12/site.py"; fi
grep -q '^ENABLE_USER_SITE = None' "$SITE_PY" || { echo "❌ $SITE_PY has changed: no ENABLE_USER_SITE to turn off."; exit 1; }
sed -i.orig 's/^ENABLE_USER_SITE = None/ENABLE_USER_SITE = False  # Stenny: never the user'"'"'s own packages/' "$SITE_PY"
rm -f "$SITE_PY.orig"

# Every .pyc ready, checked by hash never (the app's files don't change under it), and the old timestamp ones gone
find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +
"$PY" -I -m compileall -q -j 0 --invalidation-mode unchecked-hash "$OUT" >/dev/null || true

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")"
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
[[ -z "$(git -C "$ROOT" status --porcelain -- src pyproject.toml uv.lock 2>/dev/null)" ]] || COMMIT="$COMMIT+changes"
# The engine knows from this file that it runs from inside the app (src/stenny/paths.py, in_app())
printf '{"version": "%s", "commit": "%s", "python": "%s", "target": "%s"}\n' "$VERSION" "$COMMIT" "$PYTHON" "$TRIPLE" \
    > "$OUT/stenny-engine.json"

# Does it start, and can it load what transcribing needs? (No model: that's a download.)
check="import site, stenny.cli, stenny.app_server, stenny.mcp_server; assert not site.ENABLE_USER_SITE; import $( [[ "$TRIPLE" == *windows* ]] \
    && echo "faster_whisper, ctranslate2" || echo "mlx.core, mlx_whisper.transcribe")"
PYTHONDONTWRITEBYTECODE=1 "$PY" -s -c "$check"
[[ "$(PYTHONDONTWRITEBYTECODE=1 "$PY" -s -m stenny --version 2>&1)" == *"$VERSION"* ]] \
    || { echo "❌ The bundled engine doesn't say it's $VERSION."; exit 1; }

# Windows' paths end at 260 characters, and the install folder (C:\Users\<name>\AppData\Local\Stenny\engine\) takes
# ~50 of them. The longest today are the anthropic package's .pyc files, ~155 characters (a Mac's lib/python3.12/
# makes them 11 longer); much more and an unlucky user name could cross the limit.
LONGEST="$(cd "$OUT" && find . -type f | awk '{ print length($0) }' | sort -n | tail -1)"
(( LONGEST < 185 )) || { echo "❌ A file in the engine has a path of $LONGEST characters: too long for Windows."; exit 1; }
SIZE="$(du -sh "$OUT" | cut -f1)"
FILES="$(find "$OUT" -type f | wc -l | tr -d ' ')"
echo "✅ engine in desktop/src-tauri/engine: Stenny $VERSION ($COMMIT), Python $PYTHON, $TRIPLE, $SIZE in $FILES files (longest path $LONGEST)"
