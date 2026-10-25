"use strict";

document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-caption]");
    if (!button) return;

    const caption = document.getElementById(button.dataset.copyCaption);
    const status = document.querySelector(`[data-copy-status="${button.dataset.copyCaption}"]`);
    if (!caption || !status) return;

    try {
        await navigator.clipboard.writeText(caption.value);
        status.textContent = "Caption copied.";
    } catch {
        caption.focus();
        caption.select();
        status.textContent = "Select and copy the caption.";
    }
});
