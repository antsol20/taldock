#!/usr/bin/env bash
#
# Read back a login: when each part of the session started, and what
# taldock spent its own startup on.
#
# The two halves answer different questions. The process table shows how
# long xfce4-session took to get around to us -- the part that dwarfs
# everything else. The timing marks (TALDOCK_TIMING=1, written to stderr and
# so to ~/.xsession-errors) show what the dock did once it was running.
#
#   ./tools/login-timeline.sh
set -euo pipefail

echo "== session startup order =================================================="
ps -eo lstart,cmd --sort=start_time \
    | grep -E 'xfce4-session|xfwm4|xfsettingsd|Thunar|xfdesktop|taldock|xfce4-power|notifyd' \
    | grep -v grep \
    | cut -c5-

echo
echo "== taldock startup ========================================================"
if grep -q 'taldock-timing' ~/.xsession-errors 2>/dev/null; then
    grep 'taldock-timing' ~/.xsession-errors | sed 's/^taldock-timing/ /'
else
    echo " No marks in ~/.xsession-errors."
    echo " Start taldock with TALDOCK_TIMING=1 -- see tools/session-slot.sh."
fi

echo
echo "== xfce4-session complaints ==============================================="
grep -iE 'xfce4-session.*(WARNING|Unable|failed)' ~/.xsession-errors 2>/dev/null \
    || echo " (none)"
