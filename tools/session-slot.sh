#!/usr/bin/env bash
#
# Start taldock as an xfce4-session *client* rather than an autostart entry.
#
# Why this exists: xfce4-session starts its session clients first and only
# reaches the autostart batch once every client has registered with the
# session manager or timed out. On the machine this was written for that is
# 17 seconds after the session begins -- 17 seconds of no dock, none of it
# taldock's own doing.
#
# The catch is the priority: clients are started in groups of equal
# priority, and a group whose clients never register costs ~8 seconds.
# GTK3 dropped XSMP, so taldock never registers -- give it a group of its
# own and every later group, xfdesktop included, is pushed 8 seconds back.
# Priority 30 puts it with "Thunar --daemon", which does not register
# either, so the wait it joins is one the session was already paying.
#
# (The removed "xfce4-panel" that used to sit at priority 25 cost nothing,
# despite the warning in ~/.xsession-errors: a spawn that fails leaves the
# group with nothing to wait for.)
#
#   ./tools/session-slot.sh install [--timing]
#   ./tools/session-slot.sh restore
#
# The autostart entry is deliberately left in place as a fallback: a second
# dock refuses to start, so whichever mechanism fires first simply wins.
set -euo pipefail

CHANNEL="xfce4-session"
SESSION="${TALDOCK_SESSION:-Failsafe}"
BIN="${TALDOCK_BIN:-$HOME/.local/bin/taldock}"
BACKUP="${XDG_CONFIG_HOME:-$HOME/.config}/taldock/session-slot.bak"

say()  { printf '\033[1;35m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

prop() { xfconf-query -c "$CHANNEL" -p "$1" 2>/dev/null; }

# Share the priority of a client that already stalls its group; see above.
PRIORITY=30

# The slot to take: the one running xfce4-panel, else the first free index.
find_slot() {
    local count index cmd
    count="$(prop "/sessions/$SESSION/Count" || echo 0)"
    for ((index = 0; index < count; index++)); do
        cmd="$(prop "/sessions/$SESSION/Client${index}_Command" | tr -d '\n')"
        case "$cmd" in
            *xfce4-panel*|*taldock*) echo "$index"; return 0 ;;
        esac
    done
    echo "$count"
}

backup() {
    mkdir -p "$(dirname "$BACKUP")"
    {
        echo "# xfce4-session $SESSION client list, before taldock took a slot."
        echo "# Written by tools/session-slot.sh on $(date -Is)."
        xfconf-query -c "$CHANNEL" -lv | grep "^/sessions/$SESSION/"
    } > "$BACKUP"
    say "Backed up the client list to $BACKUP"
}

install_slot() {
    local timing=0 slot base count
    [ "${1:-}" = "--timing" ] && timing=1

    command -v xfconf-query >/dev/null || { warn "xfconf-query not found."; exit 1; }
    [ -x "$BIN" ] || { warn "$BIN is not executable; run ./install.sh first."; exit 1; }

    [ -f "$BACKUP" ] || backup
    slot="$(find_slot)"
    base="/sessions/$SESSION/Client${slot}"

    # Client<N>_Command is an argv array. /usr/bin/env is only used when an
    # environment variable has to be set; a bare command needs no wrapper.
    if [ "$timing" = 1 ]; then
        xfconf-query -c "$CHANNEL" -p "${base}_Command" \
            -t string -s /usr/bin/env \
            -t string -s TALDOCK_TIMING=1 \
            -t string -s "$BIN"
    else
        xfconf-query -c "$CHANNEL" -p "${base}_Command" -t string -s "$BIN"
    fi
    xfconf-query -c "$CHANNEL" -p "${base}_Priority" -n -t int -s "$PRIORITY" 2>/dev/null \
        || xfconf-query -c "$CHANNEL" -p "${base}_Priority" -t int -s "$PRIORITY"
    xfconf-query -c "$CHANNEL" -p "${base}_PerScreen" -n -t bool -s false 2>/dev/null \
        || xfconf-query -c "$CHANNEL" -p "${base}_PerScreen" -t bool -s false

    count="$(prop "/sessions/$SESSION/Count" || echo 0)"
    if [ "$slot" -ge "$count" ]; then
        xfconf-query -c "$CHANNEL" -p "/sessions/$SESSION/Count" -t int -s "$((slot + 1))"
    fi

    say "taldock now starts as session client $slot (priority $PRIORITY)."
    xfconf-query -c "$CHANNEL" -lv | grep "^/sessions/$SESSION/Client${slot}"
    echo
    say "Log out and back in to test. Undo with: $0 restore"
}

restore_slot() {
    [ -f "$BACKUP" ] || { warn "No backup at $BACKUP; nothing to restore."; exit 1; }
    # The backup is xfconf-query -lv output, so an array reads back as
    # "[a,b]". That round-trips only because session commands are plain
    # argv words -- an argument containing a comma would not survive.
    local prop_name value args
    while read -r prop_name value; do
        case "$prop_name" in "/sessions/$SESSION/"*) ;; *) continue ;; esac
        case "$value" in
            \[*\])   # an argv array: [a,b,c]
                IFS=',' read -r -a args <<< "${value:1:${#value}-2}"
                set --
                for arg in "${args[@]}"; do set -- "$@" -t string -s "$arg"; done
                xfconf-query -c "$CHANNEL" -p "$prop_name" "$@" ;;
            true|false)
                xfconf-query -c "$CHANNEL" -p "$prop_name" -t bool -s "$value" ;;
            *[!0-9]*)
                xfconf-query -c "$CHANNEL" -p "$prop_name" -t string -s "$value" ;;
            *)
                xfconf-query -c "$CHANNEL" -p "$prop_name" -t int -s "$value" ;;
        esac
    done < <(grep -v '^#' "$BACKUP")
    say "Restored the $SESSION client list from $BACKUP"
}

case "${1:-}" in
    install) shift; install_slot "${1:-}" ;;
    restore) restore_slot ;;
    *) sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
