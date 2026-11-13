"use strict";

// Shared by the rich editors, captions and shotlist. The fallback also works on plain HTTP/mobile.
window.jrcCopyText = async function (text, button) {
    const focused = document.activeElement;
    try {
        if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
        else {
            const area = document.createElement("textarea");
            area.value = text;
            area.style.cssText = "position:fixed;left:0;top:0;opacity:0";
            document.body.append(area);
            area.focus(); area.select(); area.setSelectionRange(0, area.value.length);
            let copied;
            try { copied = document.execCommand("copy"); } finally { area.remove(); focused?.focus(); }
            if (!copied) throw new Error("Copy failed");
        }
        button.textContent = "Copied";
    } catch { button.textContent = "Copy failed — try again"; }
    button.setAttribute("aria-live", "polite");
    clearTimeout(button.copyTimer);
    button.copyTimer = setTimeout(() => { button.textContent = "Copy"; }, 2500);
};

async function submit(form) {
    const response = await fetch(form.action, { method: "POST", body: new FormData(form),
        headers: { Accept: "application/json" }, credentials: "same-origin" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || response.redirected) throw new Error(data.error || "Could not save. Check your access and try again.");
    return data;
}
const status = document.querySelector("[data-detail-status]");
const rename = document.querySelector("[data-inline-rename]");
if (rename) {
    const start = document.querySelector("[data-rename-start]");
    const heading = document.querySelector("[data-content-heading]");
    const input = rename.elements.title;
    let saved = input.value, pending = false, cancelled = false;
    function close() { rename.hidden = true; heading.hidden = false; }
    start.addEventListener("click", () => {
        input.value = saved; cancelled = false;
        heading.hidden = true; rename.hidden = false; input.focus(); input.select();
    });
    async function save() {
        if (pending || cancelled || rename.hidden) return;
        if (input.value.trim() === saved) { close(); return; }
        if (!input.reportValidity()) return;
        pending = true; input.readOnly = true;
        try {
            const data = await submit(rename);
            saved = data.title; start.textContent = saved; close(); status.textContent = data.message;
            document.title = saved + document.title.slice(document.title.indexOf(" · "));
        } catch (error) { status.textContent = error.message; }
        finally { pending = false; input.readOnly = false; }
    }
    rename.addEventListener("submit", event => { event.preventDefault(); save(); });
    input.addEventListener("blur", save);
    input.addEventListener("keydown", event => {
        if (event.key === "Escape" && !pending) { event.preventDefault(); cancelled = true; input.value = saved; close(); start.focus(); }
    });
}
const category = document.querySelector("[data-category-form]");
if (category) category.addEventListener("change", async () => {
    const select = category.elements.category;
    const request = submit(category);
    select.disabled = true;
    try { const data = await request; select.dataset.saved = data.category; status.textContent = "Category saved."; }
    catch (error) { select.value = select.dataset.saved; status.textContent = error.message; }
    finally { select.disabled = false; }
});
document.addEventListener("click", event => {
    const button = event.target.closest("[data-copy-target], [data-copy-shotlist]");
    if (!button) return;
    const text = button.hasAttribute("data-copy-shotlist")
        ? [...document.querySelectorAll(".shotlist-row")].map(row => {
            const text = row.querySelector("[data-shotlist-copy-text]").textContent;
            const note = row.querySelector(".shotlist-note")?.textContent;
            return text + (note ? " — " + note : "");
        }).join("\n")
        : document.getElementById(button.dataset.copyTarget).value;
    window.jrcCopyText(text, button);
});
document.addEventListener("submit", async event => {
    const form = event.target.closest("[data-version-name]");
    if (!form || form.dataset.richName !== undefined) return;
    event.preventDefault();
    const button = form.querySelector("button"), message = form.querySelector("[role=status]");
    if (button.disabled) return;
    button.disabled = true;
    try {
        const data = await submit(form);
        form.elements.name.value = data.name;
        document.querySelectorAll("[data-version-label]").forEach(label => {
            if (label.dataset.versionLabel === form.dataset.version) label.textContent = `V${data.number}${data.name ? ' – ' + data.name : ''}`;
        });
        message.textContent = "Name saved.";
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
});
