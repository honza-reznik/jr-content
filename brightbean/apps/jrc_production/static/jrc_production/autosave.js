// JR Content: přidáno 2026-11-14 (blok P2-B08), AGPL-3.0.
// Automatic saving shared by the Concept, Script, Caption and Shotlist editors of the content detail.
// One saver per field: it waits briefly after typing, sends the newest text, shows "Saving… / Saved",
// keeps the text and retries with growing pauses when the connection fails, and tells the tab switch
// (content-tabs.js) and the browser whether anything is still unsaved.
"use strict";

(function () {
    if (window.jrcAutosave) return;
    const savers = new Set();
    const guards = new Set();

    // The version rule ("same person, same device, 15 minutes") needs a stable ID of this browser.
    function deviceId() {
        const make = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID()
            : `d-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`);
        try {
            let id = localStorage.getItem("jrc-device");
            if (!id || !/^[A-Za-z0-9-]{8,64}$/.test(id)) {
                id = make();
                localStorage.setItem("jrc-device", id);
            }
            return id;
        } catch {
            window.jrcDeviceFallback = window.jrcDeviceFallback || make();
            return window.jrcDeviceFallback;
        }
    }

    function csrfToken(root) {
        const field = (root || document).querySelector("[name=csrfmiddlewaretoken]") || document.querySelector("[name=csrfmiddlewaretoken]");
        return field ? field.value : "";
    }

    // Network problems and server errors are retried; a refusal (4xx) is shown and not repeated.
    async function postJSON(url, data, root) {
        let response;
        try {
            response = await fetch(url, {
                method: "POST", credentials: "same-origin", cache: "no-store",
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken(root), Accept: "application/json" },
                body: JSON.stringify(data),
            });
        } catch {
            const error = new Error("No connection.");
            error.retry = true;
            throw error;
        }
        const result = await response.json().catch(() => null);
        if (response.redirected || response.status === 401) {
            const error = new Error("Your session ended. Copy your text, then reload the page and sign in.");
            error.status = 401;
            throw error;
        }
        if (!response.ok || !result) {
            const error = new Error((result && result.error) || (response.status >= 500
                ? "The server did not save the change." : "The change could not be saved. Reload the page."));
            error.status = response.status;
            error.retry = response.status >= 500 || response.status === 0 || !result;
            throw error;
        }
        return result;
    }

    function setStatus(element, state, text) {
        if (!element) return;
        element.dataset.state = state;
        element.textContent = text;
    }

    /**
     * create({ root, status, send, delay }) -> saver
     * send() reads the current value itself and returns { message } (optional) or throws (see postJSON).
     */
    function create({ root, status, send, delay = 900, savedText = "Saved" }) {
        let generation = 0, saved = 0, timer = null, inflight = null, failures = 0, stopped = false, refused = false;
        const saver = {
            root,
            changed() {
                if (stopped) return;
                generation += 1;
                refused = false;
                setStatus(status, "pending", "Unsaved changes…");
                clearTimeout(timer);
                timer = setTimeout(run, delay);
            },
            dirty: () => generation !== saved,
            async flush() {
                clearTimeout(timer);
                timer = null;
                if (inflight) await inflight;
                if (generation !== saved && !refused && !stopped) await run();
                return generation === saved;
            },
            stop() {
                stopped = true;
                clearTimeout(timer);
                savers.delete(saver);
            },
        };
        async function run() {
            clearTimeout(timer);
            timer = null;
            if (stopped || inflight || generation === saved) return inflight;
            const captured = generation;
            setStatus(status, "saving", "Saving…");
            inflight = (async () => {
                try {
                    const result = await send();
                    saved = captured;
                    failures = 0;
                    if (generation === saved) setStatus(status, "saved", (result && result.message) || savedText);
                } catch (error) {
                    if (error.retry) {
                        failures += 1;
                        const wait = Math.min(30000, 1000 * 2 ** Math.min(failures, 5));
                        setStatus(status, "offline", navigator.onLine === false
                            ? "Offline. Your changes are kept here and saved when the connection is back."
                            : "Not saved yet. Your changes are kept here; retrying…");
                        if (!stopped) timer = setTimeout(run, wait);
                    } else {
                        // Saving the same text again would be refused again; the next edit tries again.
                        refused = true;
                        setStatus(status, "error", error.message);
                    }
                } finally {
                    inflight = null;
                }
                if (!stopped && !refused && !timer && generation !== saved) timer = setTimeout(run, delay);
            })();
            return inflight;
        }
        savers.add(saver);
        return saver;
    }

    function dirty() {
        return [...savers].some((saver) => saver.dirty());
    }

    async function flushAll() {
        const results = await Promise.all([...savers].map((saver) => saver.flush().catch(() => false)));
        return results.every(Boolean);
    }

    // Before the open tab is replaced: save what can be saved; ask before anything would be lost.
    async function canLeave() {
        const timeout = new Promise((resolve) => setTimeout(() => resolve(false), 8000));
        const savedAll = await Promise.race([flushAll(), timeout]);
        if (!savedAll && dirty() && !window.confirm("Some changes have not been saved yet (no connection or the server refused them). Leave this tab and lose them?")) return false;
        for (const guard of guards) {
            if (!(await guard())) return false;
        }
        return true;
    }

    document.addEventListener("jrc:panel-unload", (event) => {
        const panel = event.detail && event.detail.panel;
        for (const saver of [...savers]) {
            if (!saver.root || !panel || panel.contains(saver.root)) saver.stop();
        }
    });
    window.addEventListener("online", () => { savers.forEach((saver) => { if (saver.dirty()) saver.flush(); }); });
    window.addEventListener("beforeunload", (event) => {
        if (dirty()) { event.preventDefault(); event.returnValue = ""; }
    });

    window.jrcAutosave = { create, postJSON, deviceId, dirty, flushAll, canLeave, guards, setStatus };
})();
