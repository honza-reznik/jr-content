// JR Content: přidáno 2026-11-04 (blok P1-B10), AGPL-3.0.
// Choosing an image uploads it right away (one tap on a phone); the server checks it again.
"use strict";

document.querySelectorAll("[data-thumbnail-form]").forEach((form) => {
    const input = form.querySelector("[data-thumbnail-input]");
    const submit = form.querySelector("[data-thumbnail-submit]");
    const status = form.querySelector("[data-thumbnail-status]");
    const maxBytes = Number(form.dataset.maxBytes || 0);
    if (!input || !status) return;
    if (submit) submit.hidden = true;

    input.addEventListener("change", () => {
        const file = input.files && input.files[0];
        if (!file) return;
        if (maxBytes && file.size > maxBytes) {
            status.textContent = "The image is larger than 20 MB.";
            status.classList.add("text-red-700");
            input.value = "";
            return;
        }
        if (file.type && !file.type.startsWith("image/")) {
            status.textContent = "Upload a JPG, PNG or WebP image.";
            status.classList.add("text-red-700");
            input.value = "";
            return;
        }
        status.classList.remove("text-red-700");
        status.textContent = "Uploading…";
        form.classList.add("is-uploading");
        form.submit();
    });
});
