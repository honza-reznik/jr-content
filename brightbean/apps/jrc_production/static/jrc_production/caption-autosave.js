// JR Content: přidáno 2026-11-14 (blok P2-B08), AGPL-3.0.
// Caption tab: each channel's caption saves automatically (no Save button). The server applies the
// version rule and the approval mode; the version label, the saved versions and the comparison are
// redrawn from its answer.
(function () {
  "use strict";
  if (!window.jrcAutosave) return;
  var device = window.jrcAutosave.deviceId();
  document.querySelectorAll("[data-caption-channel][data-autosave-url]").forEach(function (card) {
    if (card.dataset.autosaveReady) return;
    card.dataset.autosaveReady = "1";
    var field = card.querySelector("[data-caption-field]");
    var status = card.querySelector("[data-save-status]");
    var notice = card.querySelector("[data-caption-notice]");
    if (!field) return;
    var saver = window.jrcAutosave.create({
      root: card,
      status: status,
      send: function () {
        var text = field.value;
        return window.jrcAutosave.postJSON(card.dataset.autosaveUrl, {
          text: text,
          base: card.dataset.base || null,
          revision: Number(card.dataset.revision || 0),
          device: device,
        }, card).then(function (result) {
          field.defaultValue = text; // what the server has (thumbnail.js asks before uploading)
          if (result.version) {
            card.dataset.base = result.version.id;
            card.dataset.revision = String(result.version.revision);
          }
          if (result.versions_html !== undefined) {
            var holder = card.querySelector("[data-caption-versions]");
            // Never redraw under a version name somebody is typing.
            if (holder && !holder.contains(document.activeElement)) holder.innerHTML = result.versions_html;
          }
          var label = card.querySelector("[data-caption-version]");
          if (label && result.version) {
            label.textContent = result.version.label;
          }
          var hint = card.querySelector("[data-reopens-review]");
          if (hint) { hint.textContent = result.hint || ""; hint.hidden = !result.hint; }
          var messages = [];
          if (result.concurrent) {
            messages.push(result.concurrent.author + " saved V" + result.concurrent.number +
              " while you were editing. Your text is kept as its own version; compare the versions to combine them.");
          }
          if (result.notice) messages.push(result.notice);
          if (notice && messages.length) { notice.textContent = messages.join(" "); notice.classList.remove("hidden"); }
          if (!result.changed) return { message: "No changes" };
          return { message: result.created ? "Saved as V" + result.version.number : "Saved to V" + result.version.number };
        });
      },
    });
    field.addEventListener("input", function () { saver.changed(); });
    field.addEventListener("blur", function () { if (saver.dirty()) saver.flush(); });
  });
})();
