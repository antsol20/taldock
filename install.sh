#!/usr/bin/env bash
#
# Installs taldock for the current user (no root needed except for the two
# runtime packages). Re-runnable: every step is idempotent.
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
BIN="$PREFIX/bin"
LIB="$PREFIX/share/taldock"
APPS="$PREFIX/share/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

say()  { printf '\033[1;35m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------- packages
need_pkgs=()
python3 -c 'import cairo' 2>/dev/null || need_pkgs+=(python3-gi-cairo)
python3 - <<'PY' 2>/dev/null || need_pkgs+=(gir1.2-wnck-3.0)
import gi; gi.require_version("Wnck", "3.0")
from gi.repository import Wnck
PY
if [ ${#need_pkgs[@]} -gt 0 ]; then
    say "Installing runtime packages: ${need_pkgs[*]}"
    sudo apt-get install -y "${need_pkgs[@]}"
fi

# ------------------------------------------------------------------ files
say "Installing to $LIB"
mkdir -p "$BIN" "$LIB" "$APPS" "$AUTOSTART"
rm -rf "$LIB/taldock"
cp -r "$SRC/taldock" "$LIB/taldock"
find "$LIB/taldock" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

cat > "$BIN/taldock" <<EOF
#!/bin/sh
exec env PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}" python3 -m taldock "\$@"
EOF
chmod +x "$BIN/taldock"

sed "s|^Exec=taldock$|Exec=$BIN/taldock|" "$SRC/taldock.desktop" > "$APPS/taldock.desktop"
cp "$APPS/taldock.desktop" "$AUTOSTART/taldock.desktop"

# ------------------------------------------------- browser tab bridge (opt)
HOST_SRC="$SRC/extension/host/taldock-tabs-host.py"
HOST_DST="$LIB/taldock-tabs-host.py"
cp "$HOST_SRC" "$HOST_DST"; chmod +x "$HOST_DST"

installed_manifest=0
for dir in \
    "$HOME/.config/google-chrome/NativeMessagingHosts" \
    "$HOME/.config/chromium/NativeMessagingHosts" \
    "$HOME/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts" \
    "$HOME/.config/microsoft-edge/NativeMessagingHosts"; do
    parent="$(dirname "$dir")"
    [ -d "$parent" ] || continue
    mkdir -p "$dir"
    sed "s|@HOST_PATH@|$HOST_DST|" \
        "$SRC/extension/host/com.taldock.tabs.json.in" > "$dir/com.taldock.tabs.json"
    installed_manifest=1
    say "Tab bridge registered for $(basename "$parent")"
done
[ "$installed_manifest" = 1 ] || warn "No Chromium-family browser profile found; skipped tab bridge."

# ------------------------------------------------------------------- done
say "Installed."
cat <<EOF

  Run it now:        $BIN/taldock --replace
  It will autostart on your next login.

  To stop using it and get xfce4-panel back:
      pkill -f 'taldock' ; xfce4-panel &
      rm -f "$AUTOSTART/taldock.desktop"

  Browser tab stacking (optional):
      1. Open chrome://extensions, enable Developer mode
      2. "Load unpacked" -> $SRC/extension/chrome
      3. Tabs then stack under the browser icon in the dock.

EOF
