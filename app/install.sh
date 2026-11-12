#!/bin/zsh
# Installs app/build.noindex/Stenny.app and opens it. Used by build.sh and by `stenny update` (an older Stenny runs the
# new release's copy of this file, so it must stay here and keep this name).
#   app/install.sh            install and open
#   app/install.sh --no-open  install only
#
# Since 0.5.0 that's the new app (desktop/), and it goes through desktop/scripts/install-mac.sh like every other Mac
# install: in place, other copies unregistered, one Stenny in the Dock. /Applications, or ~/Applications when this
# account may not write there or another account's copy is there.
set -euo pipefail
here="${0:A:h}"
[[ -d "$here/build.noindex/Stenny.app" ]] || { echo "❌ $here/build.noindex/Stenny.app doesn't exist."; exit 1; }
exec "$here/../desktop/scripts/install-mac.sh" "$@" "$here/build.noindex/Stenny.app"
