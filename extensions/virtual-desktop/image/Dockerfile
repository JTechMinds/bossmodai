# BossMod Virtual Desktop: one agent's Linux desktop (XFCE on Xvfb).
#
# Built by the extension's one-click setup (install.py) with the classic
# builder of the Docker API, so this file uses no BuildKit-only syntax.
# The base is pinned by tag, not digest, so a rebuild picks up security
# updates; Node.js is pinned exactly by version and checksum. Changing
# anything here means bumping image_version (Dockerfile LABEL below and the
# manifest's defaults.image_version and setup.ready_requires).
FROM debian:trixie-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_VERSION=24.22.1

# pipefail: the Node checksum check below is a pipeline, and a failed
# download or a missing checksum line must fail the build.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# One layer: the desktop, system tools, and the coding tools the agent would
# otherwise install first. No screensaver or power manager. The CJK and emoji
# fonts make international pages and text render as glyphs, not boxes.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      xvfb xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-terminal thunar mousepad \
      dbus-x11 xdotool scrot x11-utils firefox-esr fonts-dejavu fonts-noto-core dmz-cursor-theme \
      fonts-noto-cjk fonts-noto-color-emoji \
      sudo ca-certificates locales tini procps \
      python3 python3-pip python3-venv python3-dev python-is-python3 pipx \
      build-essential pkg-config libssl-dev libffi-dev \
      git git-lfs openssh-client \
      curl wget unzip zip xz-utils jq ripgrep fd-find tree less nano vim htop file rsync sqlite3 \
 && rm -rf /var/lib/apt/lists/*

# Node.js LTS from the official release, not Debian's nodejs, so the agent
# gets a supported LTS. The tarball is verified against the same release's
# SHASUMS256.txt; a mismatch fails the build.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) node_arch=x64 ;; \
      arm64) node_arch=arm64 ;; \
      *) echo "Node.js: unsupported architecture $arch" >&2; exit 1 ;; \
    esac; \
    tarball="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    cd /tmp; \
    curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/${tarball}"; \
    curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt"; \
    grep " ${tarball}\$" SHASUMS256.txt | sha256sum -c -; \
    tar -xJf "$tarball" -C /usr/local --strip-components=1 --no-same-owner; \
    rm "$tarball" SHASUMS256.txt; \
    node --version; \
    npm --version

# No extra steps for the agent:
# - pip installs as `agent` go to ~/.local (the persistent home volume)
#   instead of failing on Debian's externally-managed-Python rule; pip
#   cannot write the system site-packages, so apt's files are never touched;
# - `npm install -g` needs no sudo: its prefix is the agent's ~/.local;
# - Debian names fd's binary fdfind;
# - new repositories start on main.
ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN printf 'prefix=/home/agent/.local\n' > /usr/local/etc/npmrc \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd \
 && git config --system init.defaultBranch main

# The agent's account: uid/gid 1000 with passwordless sudo. Desktop/ (the
# default vm upload folder), Downloads/ (Firefox's) and ~/.local/bin (which
# Debian's ~/.profile puts on PATH when it exists) are created here; a new
# named home volume is populated from them on first mount.
RUN useradd --create-home --uid 1000 --user-group --shell /bin/bash agent \
 && mkdir -p /home/agent/Desktop /home/agent/Downloads /home/agent/.local/bin \
 && chown -R agent:agent /home/agent \
 && printf 'agent ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/agent \
 && chmod 0440 /etc/sudoers.d/agent \
 && mkdir -p /etc/bossmod

# A large dark cursor the model can find on screenshots (scrot --pointer
# draws the real one). C.UTF-8 needs no locale generation and keeps
# non-ASCII text and file names intact in the terminal and file manager.
# No screen reader runs here, so GTK programs skip the accessibility bridge
# instead of warning that its bus service is missing (agents chase warnings).
ENV XCURSOR_THEME=DMZ-Black \
    XCURSOR_SIZE=32 \
    LANG=C.UTF-8 \
    NO_AT_BRIDGE=1
# GTK programs (the desktop, panel, Firefox) take the cursor theme from
# XFCE's settings, whose shipped default is an empty name: without this they
# draw GTK's smaller default (Adwaita) arrow whatever XCURSOR_THEME says.
# The grep fails the build if the shipped file no longer has these lines.
RUN sed -i \
      -e 's|<property name="CursorThemeName" type="string" value=""/>|<property name="CursorThemeName" type="string" value="DMZ-Black"/>|' \
      -e 's|<property name="CursorThemeSize" type="int" value="0"/>|<property name="CursorThemeSize" type="int" value="32"/>|' \
      /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml \
 && grep -q 'name="CursorThemeName" type="string" value="DMZ-Black"' /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml \
 && grep -q 'name="CursorThemeSize" type="int" value="32"' /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml

COPY bin/ /usr/local/bin/
# The system default panel layout: xfce4-panel finds a configuration, so it
# never shows its first-run dialog. It has no clock (see the file).
COPY xfce4-panel.xml /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml
# Every login shell joins the desktop session's D-Bus and session manager
# (see the files; the autostart entry adds the session manager's address).
COPY bm-session.sh /etc/profile.d/bm-session.sh
COPY bm-session-env.desktop /etc/xdg/autostart/bm-session-env.desktop
# Firefox opens only what it is asked to: no first-run, post-update or
# default-browser pages, no telemetry. distribution/ is a symlink into
# /usr/share/firefox-esr; the test fails the build if the file did not land.
COPY firefox-policies.json /usr/lib/firefox-esr/distribution/policies.json
RUN chmod 755 /usr/local/bin/bm-desktop-start /usr/local/bin/bm-ready /usr/local/bin/bm-capture /usr/local/bin/bm-state \
 && chmod 644 /etc/profile.d/bm-session.sh /etc/xdg/autostart/bm-session-env.desktop /usr/lib/firefox-esr/distribution/policies.json \
 && test -f /usr/share/firefox-esr/distribution/policies.json

# The session starts in the agent's home, so the terminal opens there.
WORKDIR /home/agent
LABEL bossmod.image_version="1"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/bm-desktop-start"]
