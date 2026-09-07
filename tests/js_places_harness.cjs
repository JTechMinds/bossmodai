/**
 * Node harness: every registered place exposes mount/unmount and renders
 * exactly one h1[tabindex="-1"] for navigate()'s focus move.
 * Invoked by tests/test_ui_places.py. Not a browser bundle.
 */
const fs = require("fs");

function makeEl(tag) {
    return {
        tagName: String(tag).toUpperCase(),
        nodeType: 1,
        attributes: {},
        children: [],
        setAttribute(k, v) { this.attributes[k] = v; },
        getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
        append(...kids) { kids.forEach((k) => this.children.push(k)); },
        replaceChildren() { this.children = []; },
        addEventListener() {},
        removeEventListener() {},
    };
}

global.document = {
    createElement: makeEl,
    createTextNode: (t) => ({ nodeType: 3, textContent: String(t) }),
};
global.window = { document: global.document };

eval(`${fs.readFileSync(process.argv[2], "utf8")}\n;global.BossModDom = BossModDom;\n`);
eval(`${fs.readFileSync(process.argv[3], "utf8")}\n;global.BossModPlaces = BossModPlaces;\n`);

function headings(node, found) {
    (node.children || []).forEach((child) => {
        if (child.tagName === "H1") found.push(child);
        if (child.children) headings(child, found);
    });
    return found;
}

BossModPlaces.PLACE_IDS.forEach((id) => {
    const place = BossModPlaces.get(id);
    if (!place) throw new Error(`place "${id}" is not registered`);
    if (typeof place.mount !== "function") throw new Error(`place "${id}" has no mount`);
    if (typeof place.unmount !== "function") throw new Error(`place "${id}" has no unmount`);

    const container = makeEl("div");
    place.mount(container, {});
    const found = headings(container, []);
    if (found.length !== 1) {
        throw new Error(`place "${id}" rendered ${found.length} h1 elements, expected exactly 1`);
    }
    if (found[0].getAttribute("tabindex") !== "-1") {
        throw new Error(`place "${id}" h1 must carry tabindex="-1", got ${found[0].getAttribute("tabindex")}`);
    }
    place.unmount();
});

process.stdout.write(JSON.stringify({
    ok: true,
    placeCount: BossModPlaces.PLACE_IDS.length,
    everyPlaceMounts: true,
    everyPlaceUnmounts: true,
    everyPlaceRendersOneFocusableHeading: true,
}));
