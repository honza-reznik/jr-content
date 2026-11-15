// JR Content: přidáno 2026-11-14 (blok P2-B08), AGPL-3.0.
// Channels of the content as tags: "+" adds a channel, "×" removes one, without a page load. The server
// answers with the redrawn tags; tabs that show channels (Video, Caption, Approval, Publish) reload
// their panel so they match.
"use strict";

(function () {
    const box = document.querySelector("[data-channel-tags]");
    if (!box) return;
    const CHANNEL_TABS = new Set(["video", "caption", "approval", "publish"]);
    let busy = false;

    function say(text, error) {
        const status = box.querySelector("[data-channel-status], [data-channel-error]");
        if (!status) return;
        status.textContent = text;
        status.className = error ? "channel-tags-error" : "channel-tags-status";
        status.setAttribute("role", error ? "alert" : "status");
    }

    box.addEventListener("submit", async (event) => {
        const form = event.target.closest("form");
        if (!form || !box.contains(form)) return;
        event.preventDefault();
        if (busy) return;
        const data = new FormData(form);
        const removing = data.get("action") === "remove_channel";
        if (removing) {
            // P2-B14: the output's name ("YouTube · Shorts · Ella"), as one channel may carry several outputs.
            const name = form.closest(".channel-tag")?.dataset.outputLabel;
            if (!window.confirm(`Remove ${name || "this output"} from this content?`)) return;
        }
        const panel = document.querySelector("[data-content-panel] [data-tab]");
        data.set("tab", panel ? panel.dataset.tab : "");
        busy = true;
        box.setAttribute("aria-busy", "true");
        let response = null, result = null;
        try {
            response = await fetch(box.dataset.url, {
                method: "POST", body: data, credentials: "same-origin",
                headers: {Accept: "application/json", "X-Requested-With": "XMLHttpRequest"},
            });
            result = await response.json().catch(() => null);
        } catch {
            response = null;
        }
        busy = false;
        box.removeAttribute("aria-busy");
        if (!response || response.redirected || !result || result.html === undefined) {
            say(response ? "The channels could not be changed. Reload the page and try again." : "No connection. The channels did not change; try again.", true);
            return;
        }
        box.innerHTML = result.html;
        say(result.message || result.error || "", !response.ok);
        if (response.ok && panel && CHANNEL_TABS.has(panel.dataset.tab) && window.jrcTabs) window.jrcTabs.reload();
        if (response.ok && removing) box.querySelector("[data-channel-add] summary, .channel-tag-remove button")?.focus();
    });

    // "Add channel" in a tab that needs a channel first opens the "+" right here.
    document.addEventListener("click", (event) => {
        const link = event.target.closest("[data-open-channels]");
        if (!link) return;
        const add = box.querySelector("[data-channel-add]");
        if (!add) return;
        event.preventDefault();
        add.open = true;
        add.scrollIntoView({block: "center", behavior: "smooth"});
        add.querySelector("select")?.focus();
    });
})();
