"""Composer @-mention picker, transcript pills, and the click menu."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
CSS = ROOT / "ui" / "static" / "css"
HTML = ROOT / "ui" / "templates" / "index.html"
CONVERSATION = JS / "conversation"
HARNESS = Path(__file__).resolve().parent / "js_mentions_harness.cjs"

MENTION_MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "avatar.js",
    JS / "core" / "overlay-focus.js",
    JS / "core" / "overlays.js",
    CONVERSATION / "mentions.js",
    CONVERSATION / "mention-pill.js",
    CONVERSATION / "mention-draft.js",
    CONVERSATION / "mention-picker.js",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _scripts() -> list[str]:
    return re.findall(r"static_url\('([^']+\.js)'\)", _read(HTML))


def test_mention_modules_load_before_the_surfaces_that_spend_them() -> None:
    scripts = _scripts()
    assert scripts.index("js/core/avatar.js") < scripts.index("js/conversation/mentions.js")
    assert scripts.index("js/core/overlays.js") < scripts.index("js/conversation/mention-pill.js")
    assert scripts.index("js/conversation/mentions.js") < scripts.index(
        "js/conversation/mention-pill.js"
    )
    assert scripts.index("js/conversation/mention-pill.js") < scripts.index(
        "js/conversation/mention-draft.js"
    )
    assert scripts.index("js/conversation/mention-draft.js") < scripts.index(
        "js/conversation/mention-picker.js"
    )
    assert scripts.index("js/conversation/mention-picker.js") < scripts.index(
        "js/conversation/message.js"
    )
    assert scripts.index("js/conversation/mention-picker.js") < scripts.index(
        "js/conversation/composer.js"
    )
    assert scripts.index("js/conversation/mentions.js") < scripts.index(
        "js/places/chat/chat-place.js"
    )


def test_composer_binds_the_picker_and_keeps_the_row_as_field_and_send() -> None:
    composer = _read(CONVERSATION / "composer.js")
    assert "BossModMentionPicker.bindComposer(" in composer
    assert "mentions.handleKeyDown(event)" in composer
    assert "insertMention:" in composer
    row = composer.split("class: 'composer-row' }", 1)[1].split(")", 1)[0]
    assert row.strip().startswith(", input, sendBtn"), row


def test_messages_linkify_pills_and_a_click_opens_the_menu() -> None:
    message = _read(CONVERSATION / "message.js")
    assert "BossModMentionPills.linkify(body)" in message
    pills = _read(CONVERSATION / "mention-pill.js")
    assert "function openMenu(" in pills
    assert "BossModMentions.OPEN_CHAT" in pills
    assert "BossModMentions.VIEW_DESK" in pills
    assert "BossModMentions.MENTION_AGAIN" in pills
    # A single click is the menu, not Desk. View Desk is an explicit action.
    assert "openDesk(" not in pills
    assert "contextMode: 'desk'" not in pills


def test_chat_place_configures_focus_and_desk_the_rail_already_uses() -> None:
    place = _read(JS / "places" / "chat" / "chat-place.js")
    assert "BossModMentions.configure({ store: ctx.store, navigate: ctx.navigate })" in place
    assert "BossModMentions.configure(null)" in place


def test_mention_css_is_soft_and_sits_on_the_text_line() -> None:
    css = _read(CSS / "conversation.css")
    host = css.split(".mention-host {", 1)[1].split("}", 1)[0]
    assert "position: relative" in host
    assert "display: inline" in host
    pill = css.split(".mention-pill {", 1)[1].split("}", 1)[0]
    assert "inline-flex" in pill
    assert "align-items: baseline" in pill
    assert "vertical-align: baseline" in pill
    assert "font-weight: 400" in pill
    assert "color: inherit" in pill
    assert "background: none" in pill
    assert "font-weight: 600" not in pill
    name = css.split(".mention-pill-name {", 1)[1].split("}", 1)[0]
    assert "color: inherit" in name
    menu = css.split(".menu[data-menu=\"mention\"] {", 1)[1].split("}", 1)[0]
    assert "left: 0" in menu
    assert "right: auto" in menu
    assert ".mention-picker {" in css
    composer = _read(CONVERSATION / "composer.js")
    assert "contenteditable: 'true'" in composer
    assert "BossModMentionDraft.bindEditable(input)" in composer


def test_mention_harness_filters_inserts_and_runs_menu_actions() -> None:
    result = subprocess.run(
        ["node", str(HARNESS)] + [str(path) for path in MENTION_MODULES],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "filterEmpty": ["joey", "hugh", "debra"],
        "filterQuery": ["joey"],
        "filterRole": ["hugh"],
        "filterNone": 0,
        "insertReplacesQuery": "hello @Joey ",
        "insertMentionAgain": "hello @Joey ",
        "pickerFilter": 1,
        "pillInsert": "tip @Hugh ",
        "composerPersist": True,
        "alignBaseline": True,
        "menuUnderPill": True,
        "linkifyLive": 1,
        "menuActions": ["Open Chat", "View Desk", "Mention again"],
        "openChat": "joey",
        "viewDesk": "joey",
        "mentionAgain": "joey",
        "failClosedFired": True,
        "noHardJumpOnClick": True,
    }
