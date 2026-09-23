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
    JS / "core" / "menu.js",
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
    assert "data-mention-glue" in pills
    mentions = _read(CONVERSATION / "mentions.js")
    assert "function trailingGlue(" in mentions
    draft = _read(CONVERSATION / "mention-draft.js")
    assert "mentionTokenText" in draft
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
    assert "vertical-align: middle" in host
    pill = css.split(".mention-pill {", 1)[1].split("}", 1)[0]
    assert "inline-flex" in pill
    assert "align-items: center" in pill
    assert "vertical-align: middle" in pill
    assert "min-height: 0" in pill
    # The chip paints at ~21px but has to LAY OUT at the 14px/1.5 line box,
    # or every paragraph line holding a mention spreads wider than its
    # neighbours. vertical-align:middle sizes the line from the margin box.
    assert "margin: -2px 1px" in pill
    # middle centers the chip's BOX, which left the chip's own name 2.50px
    # under the sentence baseline — the "sits low" read. Paint-only, so the
    # line box holds. See the derivation above the rule.
    assert "transform: translateY(-0.18em)" in pill
    # .mention-pill-name clips for its ellipsis, so a 1.0 line box would cut
    # the tail off any descender in an agent name.
    assert "line-height: 1.2" in pill
    assert "line-height: 1;" not in pill
    assert "vertical-align: baseline" not in pill
    assert "vertical-align: bottom" not in pill
    assert "align-items: flex-end" not in pill
    assert "font-weight: 400" in pill
    assert "color: inherit" in pill
    assert "background: var(--btn-face-hover)" in pill
    assert "background: var(--bg)" not in pill
    assert "background: none" not in pill
    assert "border: 1px solid var(--line-strong)" in pill
    assert "border: 1px solid var(--line);" not in pill
    assert "border: 0" not in pill
    assert "font-weight: 600" not in pill
    assert "var(--accent)" not in pill
    assert "tint.bg" not in pill
    assert "pink" not in pill.lower()
    name = css.split(".mention-pill-name {", 1)[1].split("}", 1)[0]
    assert "color: inherit" in name
    assert "font-weight: 400" in name
    composer_pill = css.split(".composer-input .mention-pill {", 1)[1].split("}", 1)[0]
    assert "vertical-align: middle" in composer_pill
    assert "vertical-align: bottom" not in composer_pill
    assert "align-items: flex-end" not in composer_pill
    composer_input = css.split(".composer-input {", 1)[1].split("}", 1)[0]
    assert "line-height: 1.5" in composer_input
    pills = _read(CONVERSATION / "mention-pill.js")
    assert "background:${tint.bg}" not in pills
    assert "color:${tint.ink}" not in pills
    assert "BossModAvatar.create(" in pills
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
        # A mouse pick blurs the composer before the option's click handler
        # runs. The caret shim must still replace the typed "@jo".
        "insertSurvivesBlur": "@Joey ",
        "filterEmpty": ["joey", "hugh", "debra", "auditor"],
        "filterQuery": ["joey"],
        "filterRole": ["hugh"],
        "filterNone": 0,
        "insertReplacesQuery": "hello @Joey ",
        "insertMentionAgain": "hello @Joey ",
        "pickerFilter": 1,
        "pillInsert": "tip @Hugh ",
        "composerPersist": True,
        "alignOpticalCenter": True,
        "composerAlignsWithText": True,
        "regularWeight": True,
        "louderNeutralChip": True,
        "softPillBackground": True,
        "neutralSoftGrayChip": True,
        "menuUnderPill": True,
        "linkifyLive": 1,
        "trailingPunctGlued": "TheAuditor's",
        "noStrandedPossessive": True,
        "possessiveSerialize": "tip @TheAuditor's LOCK",
        "menuActions": ["Open Chat", "View Desk", "Mention again"],
        "openChat": "joey",
        "viewDesk": "joey",
        "mentionAgain": "joey",
        "failClosedFired": True,
        "noHardJumpOnClick": True,
    }
