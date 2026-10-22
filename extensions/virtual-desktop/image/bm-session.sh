# Installed as /etc/profile.d/bm-session.sh. Gives every login shell (vm run,
# vm launch, the terminal) the desktop session's D-Bus address, so programs
# started there use the session bus. bm-desktop-start writes the file when
# the session starts; before that there is no session bus to join.
if [ -r /tmp/bm-session.env ]; then
    . /tmp/bm-session.env
fi
