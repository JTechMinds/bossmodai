"""Extensions view (browse grid, detail and its sections), live-view status reader and viewer, and the per-agent surfaces, in the fake DOM."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "ui" / "static" / "js"
HARNESS = Path(__file__).resolve().parent / "js_extensions_harness.cjs"
MODULES = [
    JS / "core" / "dom.js",
    JS / "core" / "bus.js",
    JS / "core" / "format.js",
    JS / "core" / "switch.js",
    JS / "core" / "avatar.js",
    # The marketplace's browse and detail builders the Extensions view is made of.
    JS / "marketplace" / "marketplace-withheld.js",
    JS / "marketplace" / "marketplace-items.js",
    JS / "marketplace" / "pack-card.js",
    JS / "marketplace" / "filter-rail.js",
    JS / "marketplace" / "section-reader.js",
    JS / "extensions" / "extensions-api.js",
    JS / "extensions" / "live-status.js",
    JS / "extensions" / "extensions-live.js",
    JS / "core" / "gates.js",
    JS / "core" / "secret-field.js",
    JS / "core" / "data-table.js",
    JS / "extensions" / "config-form.js",
    JS / "extensions" / "agent-config-dialog.js",
    JS / "extensions" / "extension-agents.js",
    JS / "extensions" / "extension-panel.js",
    JS / "extensions" / "extension-items.js",
    JS / "extensions" / "extension-detail.js",
    JS / "extensions" / "extensions-view.js",
    JS / "extensions" / "extensions-dialog.js",
    JS / "context" / "desk-extensions.js",
    JS / "core" / "tabs.js",
    JS / "core" / "fact-list.js",
    JS / "extensions" / "agent-view-dialog.js",
    JS / "core" / "external-open.js",
    JS / "context" / "desk-opener.js",
]


def _payload() -> dict:
    result = subprocess.run(
        ["node", str(HARNESS), *(str(path) for path in MODULES)],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_status_reader_viewer_and_card() -> None:
    payload = _payload()
    for key in (
        "detailHasNoWatchButton",
        "noFetchesWithoutSubscribers", "subscribingReadsAtOnce", "noPollingOverSixtySeconds",
        "anEventReadsExactlyOnce", "aNullEventReadsEverythingOnce", "anUnlistedEventReadsEverythingOnce",
        "aBurstOfFiveReadsAtMostTwice", "resyncReadsEverythingOnce",
        "failureKeepsLastStateAndLogs", "disabledIsAnEmptySet",
        "noSubscribersMeansNoFetches", "toggleRefreshesTheStatus",
        "twoViewsAreBothRead", "eachEventReadsItsOwnView", "aBurstAcrossTwoViewsCoalesces",
        "anUnlistedLabelThrows", "aViewTurnedOffLeavesTheList", "anUnknownIconIsLeftOutAndLogged",
        "viewerTitleAndHeadFocus", "viewerImageHasRealAlt", "viewerShowsTheQuietBand",
        "viewerInventsNothingForABlankTitle", "viewerFileUrlOpensItsFolder", "viewerOtherSchemesAreInert",
        "viewerDoesNotRefetchAnUnchangedShot", "viewerUpdatesOnANewShotAndRevokesTheOld",
        "viewerSaysSessionEnded", "viewerStopsReadingOnClose",
        "viewerTitleFromTheLabel", "viewerDesktopBandShowsTheTitleAlone",
        "viewerNoPageLineWithNeitherTitleNorUrl", "viewerRefusesAnUnlistedExtension",
    ):
        assert payload.get(key) is True, (key, payload)


def test_the_status_module_has_no_timer() -> None:
    source = (JS / "extensions" / "live-status.js").read_text(encoding="utf-8")
    assert "setInterval" not in source and "setTimeout" not in source


def test_the_agents_section_has_no_timer() -> None:
    source = (JS / "extensions" / "extension-agents.js").read_text(encoding="utf-8")
    assert "setInterval" not in source and "setTimeout" not in source


def test_the_shell_wires_the_status_to_the_bus_before_the_socket() -> None:
    shell = (JS / "shell" / "shell.js").read_text(encoding="utf-8")
    assert "BossModLiveStatus.attach({ bus });" in shell
    assert shell.index("BossModLiveStatus.attach({ bus });") < shell.index("socket.connect();")


def test_the_live_tone_colours_the_icon_soft_green() -> None:
    css = (ROOT / "ui" / "static" / "css" / "conversation.css").read_text(encoding="utf-8")
    assert '.conversation-action[data-tone="live"] svg { color: var(--ok-mark); }' in css


def test_the_multi_agent_tile_layer_is_gone() -> None:
    live = (JS / "extensions" / "extensions-live.js").read_text(encoding="utf-8")
    assert "return { openForAgent, headerCapability };" in live
    assert "ext-live-tile" not in live and "market-cards" not in live
    for name in ("extensions-dialog.js", "extensions-view.js", "extension-detail.js"):
        source = (JS / "extensions" / name).read_text(encoding="utf-8")
        assert "BossModExtensionsLive" not in source and "Watch" not in source, name


def test_the_per_agent_surfaces() -> None:
    payload = _payload()
    for key in (
        "dataTableRejectsBadOptions", "dataTablePassesRemotePaging", "dataTableNamesTheGridByCaption",
        "dataTableShowsLoading", "dataTableMapsPagesToSkipTop", "dataTableLastPageWithoutMore",
        "dataTableFirstCellIsAButton", "dataTableCellsAreText", "dataTableRowClickActivates",
        "dataTableShowsTheError", "dataTableRetryReloadsAndDestroyTearsDown",
        "deskSectionHiddenWithNone", "deskSectionShowsNotSetUp",
        "configDialogMasksTheSecret", "configDialogSendsABlankSecret", "configDialogShowsTheServerError",
        "configDialogShowsVerified", "deskDestroyClosesItsDialogs",
        "configDialogRendersTheNumberField", "configDialogSendsTheNumber", "deskRereadsWhenTheDialogCloses",
        "deskWakeWaiting", "deskWakeLastChecked", "deskWakeLastCheckedNothingNew", "deskWakeCantCheck",
        "deskNoWakeLineForNonWake",
        "viewTabsRendered", "viewOnlyTheShownTabIsRead", "viewSentTabBuildsItsOwnTable",
        "viewSwitchingBackReadsNothingNew", "viewItemOpensWithItsView", "viewCloseDestroysEveryTable",
        "viewOneListHasNoTabRow",
    ):
        assert payload.get(key) is True, (key, payload)


def test_the_browse_view() -> None:
    payload = _payload()
    for key in (
        "browseCardsShowNameVersionCategorySummaryAuthor", "cardsAreButtonsWithNoControls", "chipTonesFollowState",
        "railHasShowAndCategoriesWithCounts", "railFiltersToOnAndToACategory", "listLoadFailureOffersRetry",
        "emptySaysSo",
    ):
        assert payload.get(key) is True, (key, payload)


def test_the_detail() -> None:
    payload = _payload()
    for key in (
        "selectOpensDetailWithTrailStep", "backReturnsToTheGridAndTheCard", "heroShowsSummaryAndByline",
        "sectionsFollowTheManifest", "offSectionsSayTurnItOn", "toggleRefreshesTheStatus",
        "toggleRefusalRevertsWithTheReason", "setupMissingAsksThenStarts", "setupInstallingPollsUntilReady",
        "setupFailedOffersRetry", "detailHasNoWatchButton", "openWithAnIdLandsOnItsDetail",
        "openWithAnUnknownIdSaysSo", "closeDisposesAndStopsPolling",
    ):
        assert payload.get(key) is True, (key, payload)


def test_the_agents_section() -> None:
    payload = _payload()
    for key in (
        "agentsSectionOnlyWhenOnWithAgentConfig",
        "agentsSectionShowsLoading", "agentsSectionFailedOffersRetry", "agentsSectionRetryLoadsTheTable",
        "agentsTableIsSemantic", "agentsRowsShowEachAgent", "agentsImageIncapableOffRowIsDisabled",
        "agentsQuickEnableSavesDefaults",
        "agentsTurnOffAsksFirst", "agentsTurnOffCancelKeepsIt", "agentsTurnOffRemovesTheSettings",
        "agentsRowErrorRevertsTheSwitch", "agentsRowErrorClearsOnTheNextTry",
        "agentsSettingsOpensTheDialog", "agentsSettingsCloseRereadsAndRefocuses",
        "agentsEmptySaysSo", "agentsSummaryShowsWhenOn",
        "agentsSwitchOpensSettingsWhenAFieldIsRequired", "agentsDialogCloseRereadsAndRefocusesTheSwitch",
        "excludedAgentsListedForImageOnlyExtension",
    ):
        assert payload.get(key) is True, (key, payload)


def test_nothing_but_the_data_table_names_tabulator() -> None:
    offenders = [
        path.relative_to(JS).as_posix()
        for path in JS.rglob("*.js")
        if "vendor" not in path.parts and "Tabulator" in path.read_text(encoding="utf-8")
    ]
    assert offenders == ["core/data-table.js"], offenders


def test_the_connection_section() -> None:
    payload = _payload()
    for key in (
        "chatgptSignInInExtension", "chatgptSignInStartsAndPolls", "chatgptCompletionShowsConnection",
        "chatgptCloseStopsPolling", "providerControlsHandleUnrelatedExtension", "providerControlsKeepTheirMarkup",
        "providerChoicesHaveVisibleLabels",
    ):
        assert payload.get(key) is True, (key, payload)


def test_the_status_section() -> None:
    payload = _payload()
    for key in (
        "panelShowsMessageFactsAndAction", "panelHasNoCommandLineAndShowsTheServiceSwitch",
        "panelMasksTheSecretUntilShown", "panelShowRevealsTheSecret", "panelCopyWritesTheValue",
        "panelCopyFeedbackFadesBack", "panelActionPostsAndRerenders", "panelActionRefusalIsAnAlert",
        "panelErrorAndServiceErrorAreAlerts", "panelLoadFailureShowsTheReasonAndRetry", "panelRetryRereads",
        "failedServiceStartRevertsTheSwitchWithTheReason", "serviceSwitchOnLoadsThePanel",
    ):
        assert payload.get(key) is True, (key, payload)


def test_the_settings_section() -> None:
    payload = _payload()
    for key in (
        "settingsSectionShowsWhileOff", "settingsSectionRendersTheSharedForm", "settingsSavesInPlaceAndSaysSaved",
        "settingsRefusalKeepsTheTyping", "settingsApplyFailureRereadsTheItem", "settingsVerifiedTextIsShown",
        "settingsTypingSurvivesATabSwitch", "settingsTypingAndFocusSurviveAPollRender",
    ):
        assert payload.get(key) is True, (key, payload)


def test_the_extensions_detail_panel_has_no_ceiling() -> None:
    """A dropdown's menu hangs past its box, and a scrolling panel clips it.

    The Extensions detail manages rather than reads, so its section panel has
    no ceiling and no scroller of its own and `.market-detail` scrolls; its
    tab list pins to the top of that, except narrow, where it is the row above
    the panel. Scoped by a class, so the marketplace keeps its reading panel.
    """
    css = (ROOT / "ui" / "static" / "css" / "extensions.css").read_text(encoding="utf-8")
    sticky = ".ext-detail .market-section-list { position: sticky; top: 0; }"
    static = ".ext-detail .market-section-list { position: static; }"
    assert ".ext-detail .market-section-panel { max-height: none; overflow: visible; }" in css
    narrow_at = css.index("@media (max-width: 700px) {")
    # Wide pins and narrow does not: the override must sit in the narrow block,
    # after the rule it overrides, or the cascade keeps the list pinned there.
    assert sticky in css[:narrow_at]
    assert static in css[narrow_at:].split("\n}", 1)[0]
    detail = (JS / "extensions" / "extension-detail.js").read_text(encoding="utf-8")
    assert "class: 'market-detail ext-detail'" in detail
    market = (ROOT / "ui" / "static" / "css" / "marketplace.css").read_text(encoding="utf-8")
    panel = market.split(".market-section-panel {", 1)[1].split("}", 1)[0]
    assert "max-height: 52vh;" in panel, "the marketplace keeps its reading panel's ceiling"


def test_one_form_implementation_serves_both_settings_surfaces() -> None:
    agent = (JS / "extensions" / "agent-config-dialog.js").read_text(encoding="utf-8")
    settings = (JS / "extensions" / "extensions-dialog.js").read_text(encoding="utf-8")
    form = (JS / "extensions" / "config-form.js").read_text(encoding="utf-8")
    for source in (agent, settings):
        assert "BossModConfigForm.create(" in source
        assert "BossModSecretField" not in source and "field-input" not in source
    assert "placement: 'footer'" in agent
    assert "placement: 'inline'" in settings
    assert "BossModSecretField.bind" in form and "type: 'number'" in form
