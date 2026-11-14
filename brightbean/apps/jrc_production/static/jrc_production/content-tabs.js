"use strict";

// Content detail tabs. P1-B31: scroll arrows on narrow screens. P2-B08: switching a tab loads only its
// panel from the server (``X-JRC-Tab: 1``), keeps the browser history (each tab has its own address,
// Back and Forward work) and runs the panel's scripts; "Copy link" copies the open tab's address.
(function () {
    const wrapper = document.querySelector("[data-content-tabs]");
    const holder = document.querySelector("[data-content-panel]");
    if (!wrapper) return;
    const scroller = wrapper.querySelector("[data-tabs-scroll]");
    const previous = wrapper.querySelector(".jrc-tabs-previous");
    const next = wrapper.querySelector(".jrc-tabs-next");
    const links = [...wrapper.querySelectorAll("[data-tab-link]")];
    const update = () => {
        previous.hidden = scroller.scrollLeft <= 1;
        next.hidden = scroller.scrollLeft + scroller.clientWidth >= scroller.scrollWidth - 1;
    };
    // Opening History changes only the horizontal position, without jumping the page.
    const revealActive = () => {
        const active = scroller.querySelector('[aria-selected="true"]');
        if (active) {
            const tab = active.getBoundingClientRect();
            const bounds = scroller.getBoundingClientRect();
            if (tab.left < bounds.left + 28 || tab.right > bounds.right - 28) {
                scroller.scrollLeft += tab.left - bounds.left - (bounds.width - tab.width) / 2;
            }
        }
        update();
    };
    previous.addEventListener("click", () => scroller.scrollBy({left: -scroller.clientWidth / 2, behavior: "smooth"}));
    next.addEventListener("click", () => scroller.scrollBy({left: scroller.clientWidth / 2, behavior: "smooth"}));
    scroller.addEventListener("scroll", update, {passive: true});
    new ResizeObserver(revealActive).observe(scroller);
    revealActive();

    const ACTIVE = ["border-orange-500", "text-orange-700"];
    const INACTIVE = ["border-transparent", "text-stone-500", "hover:text-stone-800"];
    function markActive(tab) {
        for (const link of links) {
            const on = link.dataset.tabLink === tab;
            link.setAttribute("aria-selected", String(on));
            link.classList.remove(...(on ? INACTIVE : ACTIVE));
            link.classList.add(...(on ? ACTIVE : INACTIVE));
        }
        revealActive();
    }
    const tabOf = (url) => new URL(url, location.href).searchParams.get("tab") || "";

    // Copy link: the address of the tab that is open now, absolute, as it can be pasted anywhere.
    const copy = document.querySelector("[data-copy-tab-link]");
    if (copy) copy.addEventListener("click", async () => {
        const label = copy.querySelector("[data-copy-tab-link-label]");
        const panel = holder && holder.querySelector("[data-tab]");
        const url = new URL(location.href);
        if (panel && panel.dataset.tab) url.searchParams.set("tab", panel.dataset.tab);
        url.hash = "";
        let copied = false;
        try {
            if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(url.href); copied = true; }
        } catch { /* fallback below */ }
        if (!copied) {
            const area = document.createElement("textarea");
            area.value = url.href;
            area.style.cssText = "position:fixed;left:0;top:0;opacity:0";
            document.body.append(area);
            area.select();
            try { copied = document.execCommand("copy"); } catch { copied = false; }
            area.remove();
            copy.focus();
        }
        copy.dataset.copied = copied ? url.href : "";
        label.textContent = copied ? "Link copied" : "Copy failed";
        copy.setAttribute("aria-label", copied ? "Link to this tab copied" : "Copying the link failed");
        clearTimeout(copy.resetTimer);
        copy.resetTimer = setTimeout(() => { label.textContent = "Copy link"; copy.setAttribute("aria-label", "Copy link to this tab"); }, 2500);
    });

    if (!holder || !window.fetch || !window.history || !history.pushState) return;

    // Scripts inserted as HTML do not run: recreate them. Classic scripts run again for the new panel;
    // an ES module runs once per page and mounts itself on "jrc:panel-ready" (jrc_texts/dist/script.js).
    const modules = new Set([...document.querySelectorAll('script[type="module"][src]')].map((script) => script.src));
    function runScripts(container) {
        for (const old of [...container.querySelectorAll("script")]) {
            const isModule = old.type === "module";
            if (isModule && modules.has(old.src)) { old.remove(); continue; }
            const script = document.createElement("script");
            for (const attribute of old.attributes) script.setAttribute(attribute.name, attribute.value);
            if (!old.src) script.textContent = old.textContent;
            if (isModule) modules.add(old.src);
            // Classic scripts keep their order, as in a page load.
            if (!isModule && old.src) script.async = false;
            old.replaceWith(script);
        }
    }

    let loading = null;
    async function show(url, {push = false, focus = false} = {}) {
        if (loading) loading.abort();
        const controller = new AbortController();
        loading = controller;
        if (!(window.jrcAutosave ? await window.jrcAutosave.canLeave() : true)) { loading = null; return false; }
        if (loading !== controller) return false;
        holder.setAttribute("aria-busy", "true");
        let response, html;
        try {
            response = await fetch(url, {
                credentials: "same-origin", cache: "no-store", signal: controller.signal,
                headers: {"X-JRC-Tab": "1", Accept: "text/html"},
            });
            html = await response.text();
        } catch (error) {
            if (error.name === "AbortError") return false;
            response = null;
        }
        if (loading !== controller) return false;
        loading = null;
        // Sign-in redirects, errors and old servers: open the address as a normal page.
        if (!response || !response.ok || response.redirected || !response.headers.get("X-JRC-Tab")) {
            window.location.assign(url);
            return false;
        }
        const tab = response.headers.get("X-JRC-Tab");
        const old = holder.querySelector("[data-tab]");
        document.dispatchEvent(new CustomEvent("jrc:panel-unload", {detail: {panel: holder, tab: old && old.dataset.tab}}));
        holder.innerHTML = html;
        runScripts(holder);
        holder.removeAttribute("aria-busy");
        markActive(tab);
        if (push) history.pushState({jrcTab: tab}, "", url);
        document.dispatchEvent(new CustomEvent("jrc:panel-ready", {detail: {panel: holder, tab}}));
        if (focus) {
            const link = links.find((item) => item.dataset.tabLink === tab);
            if (link) link.focus();
        }
        return true;
    }
    window.jrcTabs = { show, reload: () => show(location.href) };

    const current = holder.querySelector("[data-tab]");
    history.replaceState({jrcTab: current ? current.dataset.tab : tabOf(location.href)}, "", location.href);
    wrapper.addEventListener("click", (event) => {
        const link = event.target.closest("[data-tab-link]");
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (link.getAttribute("aria-selected") === "true" && !loading) return;
        show(new URL(link.getAttribute("href"), location.href).href, {push: true, focus: true});
    });
    // Arrow keys move between tabs (the WAI-ARIA tabs pattern); Enter or a click opens one.
    wrapper.addEventListener("keydown", (event) => {
        const link = event.target.closest("[data-tab-link]");
        if (!link || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const index = links.indexOf(link);
        const target = event.key === "Home" ? links[0] : event.key === "End" ? links.at(-1)
            : links[(index + (event.key === "ArrowRight" ? 1 : -1) + links.length) % links.length];
        target.focus();
    });
    window.addEventListener("popstate", async (event) => {
        if (!event.state || !("jrcTab" in event.state)) return;
        const shown = holder.querySelector("[data-tab]");
        const target = location.href;
        if (shown && shown.dataset.tab === event.state.jrcTab) return;
        const ok = await show(target);
        if (!ok && location.href === target && shown) {
            // Staying on the open tab (unsaved changes kept): put its address back.
            history.pushState({jrcTab: shown.dataset.tab}, "", `${location.pathname}?tab=${encodeURIComponent(shown.dataset.tab)}`);
        }
    });
})();
