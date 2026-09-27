"""The place registry defines exactly six places; Chat is the only one with a context column."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_places_harness.cjs"

EXPECTED_ORDER = ["chat", "office", "tasks", "files", "metrics", "log"]


def _source() -> str:
    return (JS / "shell" / "places.js").read_text(encoding="utf-8")


def test_six_places_in_nav_order() -> None:
    source = _source()
    match = re.search(r"PLACE_IDS\s*=\s*Object\.freeze\(\[(.*?)\]\)", source, re.S)
    assert match, "PLACE_IDS must be a frozen array"
    ids = re.findall(r"'([a-z]+)'", match.group(1))
    assert ids == EXPECTED_ORDER, f"expected {EXPECTED_ORDER}, got {ids}"


def test_only_chat_has_a_context_column() -> None:
    """The 280px context column exists on Chat alone (spec 3.1)."""
    source = _source()
    assert source.count("hasContext: true") == 1, (
        "exactly one place may declare hasContext"
    )
    chat_block = source.split("chat:", 1)[1].split("office:", 1)[0]
    assert "hasContext: true" in chat_block, "Chat must be the place with the context column"


def test_every_place_has_mount_unmount_and_a_heading() -> None:
    """navigate() focuses each place's h1 — a place without one breaks keyboard nav.

    Asserted behaviourally rather than by counting source strings: the six
    stubs share one factory, so the contract has to be checked by mounting
    each place, not by how many times "mount(" is spelled.
    """
    result = subprocess.run(
        ["node", str(HARNESS), str(JS / "core" / "dom.js"), str(JS / "shell" / "places.js")],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ok": True,
        "placeCount": 6,
        "everyPlaceMounts": True,
        "everyPlaceUnmounts": True,
        "everyPlaceRendersOneFocusableHeading": True,
    }


def test_register_allows_phases_to_replace_stubs() -> None:
    source = _source()
    assert "function register(" in source, (
        "Phases 2-3 replace stubs via register(); without it they would edit this file"
    )


# Mounts the real Office with its canvas, org chart, ticker, and agent doors
# stubbed (none of them decide the tab), picks Org, remounts, and reports what
# the second mount shows. js_places_harness.cjs only mounts the registry's
# stubs on a bare element, so the real place needs the fuller fake DOM.
_OFFICE_TAB_SCRIPT = r"""
const fs = require("fs");
const { installDom } = require(process.argv[1]);
const documentStub = installDom();
global.window.dispatchEvent = () => true;
const NAMES = ["BossModDom", "BossModTabs", "BossModPlaces", "BossModFloorScope", "BossModGates", "BossModOfficePlace"];
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(process.argv[index + 2], "utf8")}\n;global.${name} = ${name};\n`);
});
const inert = { destroy() {} };
global.BossModTicker = { createTicker: () => Object.assign({ element: document.createElement("div") }, inert) };
global.BossModOfficeCanvas = {
    createOfficeCanvas: () => Object.assign({
        // Never settles: the map load is not what this checks, and settling
        // it after the synchronous unmount below would paint torn-down nodes.
        init: () => new Promise(() => {}), updateAgents() {}, resize() {},
    }, inert),
};
global.BossModOrgView = {
    createOrgView: () => Object.assign({
        element: document.createElement("div"), refresh: () => Promise.resolve(),
    }, inert),
};
global.BossModOfficeAgentActions = {};
global.BossModAgentRoutes = {};

const store = {
    getState: () => ({ roster: [], threads: [], floors: [], currentFloorId: "lobby", runtimePaused: false }),
    subscribe: () => () => {},
};
const ctx = { store, bus: { subscribe: () => () => {} }, api() {}, navigate() {} };
const place = global.BossModOfficePlace;

const first = documentStub.createElement("div");
documentStub.body.append(first);
place.mount(first, ctx);
first.querySelector("#office-tab-org").click();
place.unmount();
first.remove();

const second = documentStub.createElement("div");
documentStub.body.append(second);
place.mount(second, ctx);
const report = {
    orgTabSelected: second.querySelector("#office-tab-org").getAttribute("aria-selected") === "true",
    orgPaneShown: second.querySelector("#office-pane-org").hidden === false,
    mapPaneHidden: second.querySelector("#office-pane-map").hidden === true,
};
place.unmount();
process.stdout.write(`${JSON.stringify(report)}\n`);
"""


def test_office_tab_survives_a_remount() -> None:
    """Org picked, away and back: the Office still shows Org, not Map."""
    tests = Path(__file__).resolve().parent
    result = subprocess.run(
        [
            "node", "-e", _OFFICE_TAB_SCRIPT,
            str(tests / "js_fake_dom.cjs"),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "tabs.js"),
            str(JS / "shell" / "places.js"),
            str(JS / "shell" / "floor-scope.js"),
            str(JS / "core" / "gates.js"),
            str(JS / "places" / "office" / "office-place.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"orgTabSelected": True, "orgPaneShown": True, "mapPaneHidden": True}


# Mounts the real Office twice with a canvas whose map load settles on demand.
# The first load is still pending when the place is left and re-entered; the
# second settles, then the first. Only the second may paint, and it paints only
# the visible floor's agents, as paintFloor() does.
_OFFICE_MAP_LOAD_SCRIPT = r"""
const fs = require("fs");
const { installDom } = require(process.argv[1]);
const documentStub = installDom();
global.window.dispatchEvent = () => true;
const NAMES = ["BossModDom", "BossModTabs", "BossModPlaces", "BossModFloorScope", "BossModGates", "BossModOfficePlace"];
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(process.argv[index + 2], "utf8")}\n;global.${name} = ${name};\n`);
});
const inert = { destroy() {} };
global.BossModTicker = { createTicker: () => Object.assign({ element: document.createElement("div") }, inert) };
const canvases = [];
global.BossModOfficeCanvas = {
    createOfficeCanvas: () => {
        const made = Object.assign({
            painted: [],
            init: () => new Promise((resolve) => { made.settle = resolve; }),
            updateAgents(agents) { made.painted.push(agents.map((agent) => agent.id)); },
            resize() {},
        }, inert);
        canvases.push(made);
        return made;
    },
};
global.BossModOrgView = {
    createOrgView: () => Object.assign({
        element: document.createElement("div"), refresh: () => Promise.resolve(),
    }, inert),
};
global.BossModOfficeAgentActions = {};
global.BossModAgentRoutes = {};

const roster = [{ id: "here", floor_id: "f1" }, { id: "elsewhere", floor_id: "f2" }];
const store = {
    getState: () => ({ roster, threads: [], floors: [], currentFloorId: "f1", runtimePaused: false }),
    subscribe: () => () => {},
};
const ctx = { store, bus: { subscribe: () => () => {} }, api() {}, navigate() {} };
const place = global.BossModOfficePlace;
const flush = () => new Promise((resolve) => setImmediate(resolve));

(async () => {
    const first = documentStub.createElement("div");
    documentStub.body.append(first);
    place.mount(first, ctx);
    place.unmount();
    first.remove();

    const second = documentStub.createElement("div");
    documentStub.body.append(second);
    place.mount(second, ctx);
    const [stale, live] = canvases;
    // Drop the synchronous mount-time paints; only map-load paints count.
    stale.painted.length = 0;
    live.painted.length = 0;

    live.settle();
    await flush();
    const afterLive = JSON.stringify(live.painted);
    stale.settle();
    await flush();
    const report = {
        loadPaintsVisibleFloorOnly: afterLive === JSON.stringify([["here"]]),
        staleLoadDropped: JSON.stringify(live.painted) === afterLive && stale.painted.length === 0,
    };
    place.unmount();
    process.stdout.write(`${JSON.stringify(report)}\n`);
})().catch((err) => { console.error(err); process.exit(1); });
"""


def test_office_map_load_is_floor_scoped_and_drops_a_stale_load() -> None:
    """Away and back before the first map load settles: that load must not paint
    the new mount, and the load that does paint shows only the visible floor."""
    tests = Path(__file__).resolve().parent
    result = subprocess.run(
        [
            "node", "-e", _OFFICE_MAP_LOAD_SCRIPT,
            str(tests / "js_fake_dom.cjs"),
            str(JS / "core" / "dom.js"),
            str(JS / "core" / "tabs.js"),
            str(JS / "shell" / "places.js"),
            str(JS / "shell" / "floor-scope.js"),
            str(JS / "core" / "gates.js"),
            str(JS / "places" / "office" / "office-place.js"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"loadPaintsVisibleFloorOnly": True, "staleLoadDropped": True}
