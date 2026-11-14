// JR Content P2-B16: version-bound conversations. Text is always rendered with textContent.
(function () {
  function mount() {
  "use strict";
  document.querySelectorAll("[data-version-comments]").forEach(function (panel) {
    if (panel.dataset.ready) return;
    panel.dataset.ready = "1";
    panel.addEventListener("submit", async function (event) {
      var form = event.target.closest("[data-comment-form]");
      if (!form) return;
      event.preventDefault();
      var button = form.querySelector("button[type=submit]");
      var error = panel.querySelector("[data-comment-error]");
      if (button.disabled) return;
      button.disabled = true;
      error.textContent = "";
      try {
        // Finish edits in this tab before selecting the commented version.
        if (window.jrcAutosave && !(await window.jrcAutosave.flushAll())) throw new Error("Save your changes before commenting.");
        var data = new FormData(form);
        var editor = document.getElementById("script-panel");
        var card = panel.closest("[data-caption-channel]");
        if (editor && editor.querySelector("#script-reload")?.disabled) throw new Error("Wait for the selected version to load before commenting.");
        if (data.get("action") !== "resolve") {
          if (editor && (panel.dataset.kind === "script" || panel.dataset.kind === "concept")) {
            data.set("text_version", editor.dataset.commentVersion || "");
            data.set("revision", editor.dataset.commentRevision || "0");
          } else if (card && card.dataset.base) {
            data.set("text_version", card.dataset.base);
            data.set("revision", card.dataset.revision || "0");
          } else if (panel.dataset.kind === "shotlist") {
            var list = document.querySelector("[data-shotlist-current-format]");
            data.set("shotlist_version", list?.dataset.commentVersion || panel.dataset.version);
            data.set("format", list?.dataset.shotlistCurrentFormat || panel.dataset.format);
          }
        }
        var response = await fetch(panel.dataset.url, {method: "POST", body: data, headers: {"X-CSRFToken": data.get("csrfmiddlewaretoken")}});
        if (!response.ok || response.redirected) {
          var message = await response.json().catch(function () { return {}; });
          throw new Error(message.error || "The comment could not be saved. Reload and check your access.");
        }
        var html = await response.text();
        var next = new DOMParser().parseFromString(html, "text/html").querySelector("[data-version-comments]");
        if (!next) throw new Error("Reload and check your access. Your comment is kept here.");
        panel.replaceWith(next);
        // Same script is executed on tab switches; initialize only the newly returned panel.
        mount();
      } catch (err) { error.textContent = err.message || "Connection lost. Your comment is kept here."; }
      finally { button.disabled = false; }
    });
    panel.querySelectorAll("[data-comment-version-url]").forEach(function (details) {
      details.addEventListener("toggle", async function () {
        if (!details.open || details.dataset.loaded) return;
        var output = details.querySelector("pre");
        output.textContent = "Loading…";
        try {
          var response = await fetch(details.dataset.commentVersionUrl);
          if (!response.ok) throw new Error("Version unavailable. Check your access and retry.");
          var result = await response.json();
          output.textContent = result.text || "This version is empty.";
          details.dataset.loaded = "1";
        } catch (err) { output.textContent = err.message; }
      });
    });
  });
  }
  mount();
})();
