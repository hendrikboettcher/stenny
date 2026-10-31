#!/bin/zsh
# Builds the small ffmpeg and ffprobe that ship inside Stenny.app, so nobody needs Homebrew.
# Audio only, no GPL parts (LGPL, with libopus linked statically); works on any Apple Silicon Mac with
# macOS 14.4 or newer. build.sh copies the results from app/build.noindex/tools/ into the app.
# Takes a few minutes, once; the results are kept. Only needed to make a release.
set -euo pipefail
cd "$(dirname "$0")"

FFMPEG_VERSION=9.0.2
OPUS_VERSION=1.5.2
OUT="$PWD/build.noindex/tools"
WORK="$PWD/build.noindex/tools-src"
export MACOSX_DEPLOYMENT_TARGET=14.4

if [[ -x "$OUT/ffmpeg" && -x "$OUT/ffprobe" ]]; then
    echo "✅ Already built: $OUT"
    exit 0
fi
mkdir -p "$OUT" "$WORK"
cd "$WORK"
[[ -f "ffmpeg-$FFMPEG_VERSION.tar.xz" ]] || curl -fL -O "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
[[ -f "opus-$OPUS_VERSION.tar.gz" ]] || curl -fL -O "https://downloads.xiph.org/releases/opus/opus-$OPUS_VERSION.tar.gz"
rm -rf "ffmpeg-$FFMPEG_VERSION" "opus-$OPUS_VERSION" prefix
tar xf "ffmpeg-$FFMPEG_VERSION.tar.xz"
tar xf "opus-$OPUS_VERSION.tar.gz"
PREFIX="$WORK/prefix"

(cd "opus-$OPUS_VERSION"
 ./configure --prefix="$PREFIX" --disable-shared --enable-static --disable-doc --disable-extra-programs
 make -j"$(sysctl -n hw.ncpu)" install)

# ffmpeg's configure asks pkg-config for libopus; this one answer saves installing pkg-config
mkdir -p "$PREFIX/bin"
cat > "$PREFIX/bin/pkg-config" <<SHIM
#!/bin/sh
case " \$* " in
    *" --version "*) echo 0.29; exit 0 ;;
    *" --cflags "*) echo "-I$PREFIX/include -I$PREFIX/include/opus" ;;
    *" --libs "*) echo "-L$PREFIX/lib -lopus" ;;
    *" --modversion "*) echo "$OPUS_VERSION" ;;
esac
case "\$*" in *opus*) exit 0 ;; *) exit 1 ;; esac
SHIM
chmod +x "$PREFIX/bin/pkg-config"

(cd "ffmpeg-$FFMPEG_VERSION"
 ./configure --prefix="$PREFIX" --pkg-config="$PREFIX/bin/pkg-config" \
    --disable-doc --disable-debug --disable-network --disable-ffplay \
    --disable-encoders --enable-encoder=libopus,pcm_s16le \
    --disable-muxers --enable-muxer=opus,ogg,pcm_s16le,wav \
    --disable-decoders --enable-decoder='aac*,ac3*,alac*,amr*,eac3*,flac,mp1*,mp2*,mp3*,opus,pcm_*,vorbis,wma*' \
    --enable-libopus
 make -j"$(sysctl -n hw.ncpu)"
 cp ffmpeg ffprobe "$OUT/")
strip "$OUT/ffmpeg" "$OUT/ffprobe"
echo "✅ Built: $OUT"
