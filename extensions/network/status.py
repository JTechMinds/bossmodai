"""Local Network Access — what the Status section says (pure: no I/O, no ``sys``).

Every sentence, label and hint of the extension's Status section lives here,
apart from the listener lifecycle in the package ``__init__``. The computer's
platform is passed in as a ``sys.platform`` value, so each operating
system's variant is built, and tested, without patching it.

The panel is only reachable on the computer running BossMod (the gate
refuses the extension's routes to network sessions), so the firewall help
covers that computer's operating system and no other.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.extensions.panel import PanelFact, PanelGroup, PanelStatus

_STOPPED = "Network access isn’t running."
_OFFLINE = "This computer isn’t connected to a network. Connect it to share BossMod."
_RUNNING = "Other devices on your network can open BossMod."

_CONNECT_TITLE = "Connect another device"
_ADDRESS_HINT = "Open this in a web browser on the other device."
_CODE_HINT = "Enter it when the other device asks. You can change it in Settings."

_TROUBLE_TITLE = "If a device can’t connect"
_TROUBLE_INTRO = (
    "Check that both devices are on the same network. "
    "If they are, this computer’s firewall may be blocking port {port}."
)


@dataclass(frozen=True)
class _FirewallCommand:
    """One copy-ready command that opens the port, with what to run it in."""

    label: str
    # Formatted with the running port.
    template: str
    hint: str


# The firewall command for each platform that has a known one, by
# ``sys.platform`` value. macOS has none to run: it asks the operator itself
# the first time, so it gets a sentence instead.
_FIREWALL_COMMANDS = {
    "linux": _FirewallCommand(
        label="Firewall command (ufw)",
        template="sudo ufw allow {port}/tcp",
        hint="If your firewall is ufw, the one Ubuntu uses, run this in a terminal.",
    ),
    "win32": _FirewallCommand(
        label="Firewall command (Windows)",
        template='netsh advfirewall firewall add rule name="BossMod" dir=in action=allow protocol=TCP localport={port}',
        hint="Run this in Command Prompt opened as administrator.",
    ),
}
_MAC_PLATFORM = "darwin"
_MAC_SENTENCE = " If your Mac asks whether to accept incoming connections, choose Allow."


def stopped() -> PanelStatus:
    """Return the status of a listener that isn't running: one sentence, no groups."""
    return PanelStatus(message=_STOPPED)


def offline() -> PanelStatus:
    """Return the status of a running listener on a computer with no usable network: one sentence, no groups."""
    return PanelStatus(message=_OFFLINE)


def running(url: str, code: str, port: int, platform: str) -> PanelStatus:
    """Return the status of a running listener: how another device connects, then what to try if it can't.

    Args:
        url: The address another device types (``addresses.lan_urls``).
        code: The access code, shown masked and copyable.
        port: The port the listener serves on, named in the firewall help.
        platform: This computer's ``sys.platform`` value. ``linux`` and
            ``win32`` get their firewall command, ``darwin`` a sentence
            about the Mac's own prompt, and any other platform the
            sentence alone, since there is no known command to suggest.

    Returns:
        The running sentence and two groups: "Connect another device"
        (the address and the code) and "If a device can’t connect".

    Raises:
        pydantic.ValidationError: ``url`` or ``code`` is empty (a caller bug).
    """
    connect = PanelGroup(
        title=_CONNECT_TITLE,
        facts=[
            PanelFact(label="Address", value=url, copyable=True, hint=_ADDRESS_HINT),
            PanelFact(label="Access code", value=code, copyable=True, secret=True, hint=_CODE_HINT),
        ],
    )
    return PanelStatus(message=_RUNNING, groups=[connect, _trouble(port, platform)])


def _trouble(port: int, platform: str) -> PanelGroup:
    """The "If a device can’t connect" group for this computer's platform."""
    intro = _TROUBLE_INTRO.format(port=port)
    if platform == _MAC_PLATFORM:
        intro += _MAC_SENTENCE
    command = _FIREWALL_COMMANDS.get(platform)
    facts = [] if command is None else [
        PanelFact(label=command.label, value=command.template.format(port=port), copyable=True, hint=command.hint),
    ]
    return PanelGroup(title=_TROUBLE_TITLE, intro=intro, facts=facts)
