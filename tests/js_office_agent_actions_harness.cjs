/**
 * Node harness: the Office's agent dialog, and the routes it shares with the
 * roster. Invoked by tests/test_ui_office.py. Not a browser bundle.
 *
 * Four properties: a click raises ONE small dialog for that agent with the
 * doors in it; each door closes the dialog and lands exactly where the
 * roster's same door lands; it is not a form, so an outside click closes it;
 * and the routes refuse bad input rather than writing a half-state.
 */
const fs = require("fs");
const { installDom } = require("./js_fake_dom.cjs");

const documentStub = installDom();
const { installIconsStub } = require("./js_icons_stub.cjs");
installIconsStub();

const NAMES = ["BossModDom", "BossModOverlayFocus", "BossModOverlays", "BossModMenu", "BossModAvatar",
    "BossModAgentStatus", "BossModStore", "BossModAgentRoutes", "BossModOfficeAgentActions"];
const paths = process.argv.slice(2);
if (paths.length !== NAMES.length) {
    throw new Error(`expected ${NAMES.length} module paths, got ${paths.length}`);
}
NAMES.forEach((name, index) => {
    eval(`${fs.readFileSync(paths[index], "utf8")}\n;global.${name} = ${name};\n`);
});
const { BossModStore, BossModAgentRoutes, BossModOfficeAgentActions } = global;

async function main() {
    const jim = {
        id: "agent-jim", name: "Jim", color: null, status: "idle", currentActivityKind: null,
    };
    const navigations = [];
    const store = BossModStore.createStore({
        place: "office", conversationId: null, conversationKind: null,
        contextMode: null, deskAgentId: null,
    });
    const routes = { store, navigate: (placeId) => navigations.push(placeId) };
    const panels = () => documentStub.body.querySelectorAll(".modal-panel");
    const openFor = () => BossModOfficeAgentActions.open({
        agent: jim,
        onOpenChat: () => BossModAgentRoutes.openConversation(routes, jim.id, "agent"),
        onViewDesk: () => BossModAgentRoutes.openDesk(routes, jim.id),
    });

    // 1. One dialog, named for the agent, both doors in it, the first focused.
    openFor();
    const chat = documentStub.body.querySelector("#office-agent-open-chat");
    const desk = documentStub.body.querySelector("#office-agent-view-desk");
    const opensOneDialogForTheAgent = panels().length === 1
        && panels()[0].getAttribute("aria-label") === "Jim"
        && Boolean(chat) && Boolean(desk)
        && documentStub._activeElement === chat;

    // 2. Open chat: the dialog goes, the conversation switches, Chat is reached.
    await chat.dispatchClick();
    const openChatRoutesToChat = panels().length === 0
        && store.getState().conversationId === "agent-jim"
        && store.getState().conversationKind === "agent"
        && navigations.join(",") === "chat";

    // 3. View desk: the context column's desk for that agent, Chat reached.
    openFor();
    await documentStub.body.querySelector("#office-agent-view-desk").dispatchClick();
    const viewDeskRoutesToTheDesk = panels().length === 0
        && store.getState().contextMode === "desk"
        && store.getState().deskAgentId === "agent-jim"
        && navigations.join(",") === "chat,chat";

    // 4. Not a form: an outside click closes it, scrim and all.
    openFor();
    await documentStub.body.querySelector(".modal-backdrop").dispatchClick();
    const backdropCloses = panels().length === 0
        && documentStub.body.querySelectorAll(".modal-backdrop").length === 0;

    // 5. Already in Chat: the store switches and navigation does NOT remount it.
    store.setState({ place: "chat" });
    BossModAgentRoutes.openConversation(routes, "agent-deb", "agent");
    const routesDoNotRenavigateInsideChat = navigations.length === 2
        && store.getState().conversationId === "agent-deb";

    // 6. Refusals, not silent no-ops.
    let refusesAMissingAgent = false;
    try {
        BossModOfficeAgentActions.open({ onOpenChat() {}, onViewDesk() {} });
    } catch (err) {
        refusesAMissingAgent = true;
    }
    let refusesAnUnknownKind = false;
    try {
        BossModAgentRoutes.openConversation(routes, "x", "desk");
    } catch (err) {
        refusesAnUnknownKind = true;
    }

    process.stdout.write(JSON.stringify({
        ok: true,
        opensOneDialogForTheAgent,
        openChatRoutesToChat,
        viewDeskRoutesToTheDesk,
        backdropCloses,
        routesDoNotRenavigateInsideChat,
        refusesAMissingAgent,
        refusesAnUnknownKind,
    }));
}

main().catch((err) => {
    process.stderr.write(`${(err && err.stack) || err}\n`);
    process.exit(1);
});
