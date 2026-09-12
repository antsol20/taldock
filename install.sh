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
# The dictation widget records through pw-record; it ships in the default
# widget list, so a fresh install needs it.
command -v pw-record >/dev/null 2>&1 || need_pkgs+=(pipewire-bin)
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

# Application icon, also used for the menu button.
ICONS="$PREFIX/share/icons/hicolor/scalable/apps"
mkdir -p "$ICONS"
cp "$SRC/assets/taldock.svg" "$ICONS/taldock.svg"
gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true

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

# ----------------------------------------------------------- key bindings
# XFCE binds shortcuts through xfconf; this repoints them (saving whatever
# was there) so Super opens taldock's applications menu, and the volume keys
# drive its mixer. The volume keys matter because xfce4-panel's pulseaudio
# plugin used to grab them -- with the panel gone, nothing else does.
if command -v xfconf-query >/dev/null 2>&1; then
    PYTHONPATH="$LIB" python3 -m taldock --bind-super ||         warn "Could not bind the Super key."
    PYTHONPATH="$LIB" python3 -m taldock --bind-media ||         warn "Could not bind the volume keys."
fi

# ------------------------------------------------------------------- done
say "Installed."
cat <<EOF

  Run it now:        $BIN/taldock --replace
  It will autostart on your next login.

  Dictation (talflow): put an API key in
      ${XDG_CONFIG_HOME:-$HOME/.config}/taldock/talflow.json
  then hold Ctrl+Super+Space and speak. The shortcut is grabbed directly,
  so there is no binding step -- but it cannot be shared with another
  application that already holds it.

  To stop using it and get xfce4-panel back:
      $BIN/taldock --unbind-super ; $BIN/taldock --unbind-media
      pkill -f 'taldock' ; xfce4-panel &
      rm -f "$AUTOSTART/taldock.desktop"

  Browser tab stacking (optional):
      1. Open chrome://extensions, enable Developer mode
      2. "Load unpacked" -> $SRC/extension/chrome
      3. Tabs then stack under the browser icon in the dock.

EOF
