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
    const reject = (message) => {
        status.textContent = `${message} JPG, PNG or WebP, up to 20 MB.`;
        status.classList.add("text-red-700");
        input.setAttribute("aria-invalid", "true");
        input.value = "";
    };

    input.addEventListener("change", () => {
        const file = input.files && input.files[0];
        if (!file) return;
        // Uploading navigates back to this tab. Keep unsaved captions in every
        // channel intact instead of silently discarding them during that reload.
        const unsavedCaption = Array.from(document.querySelectorAll("[data-caption-counter]"))
            .some((field) => field.value !== field.defaultValue);
        if (unsavedCaption) {
            reject("Save caption changes before uploading a thumbnail.");
            return;
        }
        if (maxBytes && file.size > maxBytes) {
            reject("The file is larger than 20 MB.");
            return;
        }
        if (!/\.(jpe?g|png|webp)$/i.test(file.name)
            || (file.type && !["image/jpeg", "image/png", "image/webp"].includes(file.type))) {
            reject("This file type is not supported.");
            return;
        }
        status.classList.remove("text-red-700");
        input.removeAttribute("aria-invalid");
        status.textContent = "Uploading…";
        form.classList.add("is-uploading");
        form.submit();
    });
});
