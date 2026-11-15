// Záložka Video: přepínač kanálu, vložený přehrávač FreeFrame, výběr verze a "Final for publishing" (F5-B08).
// Statický soubor kvůli vynucené CSP. Přehrávač je iframe stejného původu (/video/...?embed=1);
// přihlášení vstupenkou jde skrytým formulářem POST do iframe, takže člověk nic neklepe a
// Brightbean nikdy nedá prohlížeči token FreeFrame.
(function () {
  "use strict";
  var root = document.getElementById("jrc-video");
  var dataEl = document.getElementById("jrc-video-data");
  if (!root || !dataEl) return;

  var csrf = root.dataset.csrf;
  var playerReady = root.dataset.playerReady === "1";
  var channels = JSON.parse(dataEl.textContent);
  var tabs = Array.prototype.slice.call(root.querySelectorAll("[data-video-tab]"));
  var panels = Array.prototype.slice.call(root.querySelectorAll("[data-video-channel]"));
  var activeKey = panels.length ? panels[0].dataset.videoChannel : null;
  // Per channel: the version the person chose ("" = none yet) and the one the frame shows.
  var chosen = {};
  var loaded = {};
  var newest = {};

  function show(el, on, display) {
    el.classList.toggle("hidden", !on);
    if (display) el.classList.toggle(display, on);
  }
  function card(key) { return channels.filter(function (c) { return c.key === key; })[0]; }
  function panelOf(key) { return root.querySelector('[data-video-channel="' + CSS.escape(key) + '"]'); }
  function byId(c, id) { return c.versions.filter(function (v) { return v.id === id; })[0]; }

  function finalUrl(versionId) {
    return root.dataset.statusUrl.replace(/status\/$/, "versions/") + versionId + "/final/";
  }

  function selectedVersion(c) {
    if (newest[c.key] !== (c.versions[0] && c.versions[0].id)) {
      newest[c.key] = c.versions[0] && c.versions[0].id;
      chosen[c.key] = newest[c.key];
    }
    return byId(c, chosen[c.key]);
  }

  function loadFrame(c, version) {
    var panel = panelOf(c.key);
    var frame = panel.querySelector("[data-video-frame]");
    var form = panel.querySelector("[data-video-sso]");
    if (!playerReady || !c.player_path || !version || version.status !== "ready") return;
    if (loaded[c.key] === version.id) return;
    loaded[c.key] = version.id;
    form.querySelector('input[name="next"]').value =
      c.player_path + "?embed=1&version=" + encodeURIComponent(version.ff_version_id);
    show(frame, true);
    form.submit();
  }

  function unloadFrame(key) {
    var panel = panelOf(key);
    if (!panel) return;
    var frame = panel.querySelector("[data-video-frame]");
    // Leaving the channel stops its sound; coming back signs in and loads it again.
    if (loaded[key]) frame.src = "about:blank";
    delete loaded[key];
    show(frame, false);
  }

  function element(tag, text, classes, attr) {
    var el = document.createElement(tag);
    if (text) el.textContent = text;
    el.className = classes || "";
    if (attr) el.setAttribute(attr, "");
    if (tag === "button") el.type = "button";
    return el;
  }
  var actionClasses = "rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm text-stone-700 hover:bg-stone-50 focus:ring-2 focus:ring-orange-300";

  function transcriptUrl(id) {
    return root.dataset.statusUrl.replace(/status\/$/, "versions/") + id + "/transcript/";
  }

  function renderRows(c, panel, selected) {
    var list = panel.querySelector("[data-video-versions]");
    // Park the shared player before removing a discarded row.
    Array.from(list.children).forEach(function (row) {
      if (!byId(c, row.dataset.version)) {
        var player = row.querySelector("[data-video-player]");
        if (player) panel.insertBefore(player, list);
        row.remove();
      }
    });
    if (!c.versions.length) list.appendChild(element("li", "No video yet.", "text-sm text-stone-500", "data-video-empty"));
    c.versions.forEach(function (v, index) {
      var row = list.querySelector('[data-version="' + CSS.escape(v.id) + '"]');
      if (!row) {
        row = element("li", "", "rounded-lg border border-stone-200 bg-stone-50");
        row.dataset.version = v.id;
        var details = element("details");
        var summary = element("summary", "", "cursor-pointer rounded-lg px-3 py-3 text-sm text-stone-800 focus:outline-none focus:ring-2 focus:ring-orange-300");
        summary.style.overflowWrap = "anywhere";
        summary.appendChild(element("span", "", "font-semibold", "data-version-title"));
        summary.appendChild(element("span", "", "ml-2 rounded-full bg-emerald-100 px-2 text-xs text-emerald-800", "data-video-final-mark"));
        summary.appendChild(element("span", "", "ml-2 text-xs", "data-video-status"));
        summary.appendChild(element("span", "", "mt-1 block text-xs text-stone-500", "data-version-meta"));
        details.appendChild(summary);
        var body = element("div", "", "space-y-3 border-t border-stone-200 p-3", "data-version-detail");
        body.appendChild(element("p", "", "text-sm text-stone-600", "data-video-hint"));
        var resume = element("div", "", "flex flex-wrap gap-2", "data-version-resume");
        resume.appendChild(element("button", "Continue upload", actionClasses, "data-video-resume"));
        resume.appendChild(element("button", "Discard", actionClasses, "data-video-discard-row"));
        body.appendChild(resume);
        var srt = element("section", "", "space-y-3 rounded-lg border border-stone-200 bg-white p-3", "data-transcript");
        srt.appendChild(element("h4", "SRT transcript", "text-sm font-semibold text-stone-900"));
        srt.appendChild(element("p", "", "break-words text-sm text-stone-600", "data-transcript-name"));
        var actions = element("div", "", "flex flex-wrap items-center gap-2");
        var download = element("a", "Download SRT", actionClasses, "data-transcript-download");
        download.href = transcriptUrl(v.id);
        actions.appendChild(download);
        actions.appendChild(element("button", "Preview text", actionClasses, "data-transcript-preview"));
        if (c.can_upload) {
          var label = element("label", "Upload or replace SRT (UTF-8, max 1 MB)", "block w-full text-sm text-stone-700");
          var input = element("input", "", "mt-2 block w-full min-w-0 text-sm", "data-transcript-file");
          input.type = "file"; input.accept = ".srt";
          label.appendChild(input); actions.appendChild(label);
          actions.appendChild(element("button", "Delete SRT", actionClasses, "data-transcript-delete"));
        }
        srt.appendChild(actions);
        var error = element("p", "", "hidden text-sm text-red-800", "data-transcript-error");
        error.setAttribute("role", "alert"); srt.appendChild(error);
        var preview = element("pre", "", "hidden max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-stone-50 p-3 text-sm", "data-transcript-text");
        preview.style.overflowWrap = "anywhere"; srt.appendChild(preview);
        body.appendChild(srt); details.appendChild(body); row.appendChild(details);
        details.addEventListener("toggle", function () {
          if (details.open === (chosen[c.key] === v.id)) return;
          if (details.open) chosen[c.key] = v.id;
          else if (chosen[c.key] === v.id) chosen[c.key] = null;
          render(card(c.key));
        });
      }
      row.dataset.status = v.status; row.dataset.mine = v.mine ? "1" : "0";
      row.dataset.name = v.filename; row.dataset.size = String(v.size_bytes);
      row.querySelector("[data-version-title]").textContent = "v" + v.number + " · " + v.filename;
      row.querySelector("[data-version-meta]").textContent = [v.uploaded_by, v.uploaded_at, v.duration, v.resolution].filter(Boolean).join(" · ");
      var status = row.querySelector("[data-video-status]");
      status.textContent = v.status_label;
      status.className = "ml-2 rounded-full px-2 py-0.5 text-xs " + (v.status === "ready"
        ? "bg-emerald-100 text-emerald-800" : v.status === "failed"
          ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800");
      var mark = row.querySelector("[data-video-final-mark]");
      mark.textContent = v.is_final ? "Final for publishing" : ""; show(mark, v.is_final);
      show(row.querySelector("[data-version-resume]"), v.mine && v.status === "uploading" && c.can_upload);
      var hint = row.querySelector("[data-video-hint]");
      hint.textContent = v.status === "processing" ? "Conversion runs on the server and can take several minutes. You can leave this page; the player updates when ready." : "";
      show(hint, v.status === "processing");
      var srt = row.querySelector("[data-transcript]");
      if (srt.dataset.filename !== (v.transcript_filename || "")) show(srt.querySelector("[data-transcript-text]"), false);
      srt.dataset.filename = v.transcript_filename || "";
      srt.querySelector("[data-transcript-name]").textContent = v.transcript_filename || "No transcript yet.";
      ["download", "preview", "delete"].forEach(function (action) {
        var el = srt.querySelector("[data-transcript-" + action + "]");
        if (el) show(el, !!v.transcript_filename);
      });
      row.querySelector("details").open = !!selected && selected.id === v.id;
      if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
    });
  }

  function render(c) {
    var panel = panelOf(c.key);
    if (!panel) return;
    var toolbar = panel.querySelector("[data-video-toolbar]");
    var badge = panel.querySelector("[data-video-final-badge]");
    var toggle = panel.querySelector("[data-video-final-toggle]");
    var info = panel.querySelector("[data-video-final-info]");
    var note = panel.querySelector("[data-video-player-note]");
    var version = selectedVersion(c);

    renderRows(c, panel, version);
    var player = panel.querySelector("[data-video-player]");
    var target = version && panel.querySelector('[data-version="' + CSS.escape(version.id) + '"] [data-version-detail]');
    if (target && player.parentElement !== target) target.prepend(player);
    show(player, !!version);
    show(toolbar, !!(version && version.status === "ready"), "flex");
    var download = panel.querySelector("[data-video-download]");
    if (download) {
      if (version && version.download_url) download.href = version.download_url;
      else download.removeAttribute("href");
      show(download, !!(version && version.download_url));
    }

    show(badge, !!(version && version.is_final), "inline-flex");
    if (c.can_mark_final && version) {
      toggle.textContent = version.is_final ? "Remove final mark" : "Mark as final for publishing";
      toggle.dataset.finalVersion = version.id;
      toggle.dataset.mark = version.is_final ? "0" : "1";
      show(toggle, true);
    } else {
      show(toggle, false);
    }
    if (c.final) {
      info.textContent = "Final for publishing: v" + c.final.number +
        (c.final.by ? " · marked by " + c.final.by : "") + (c.final.at ? " · " + c.final.at : "");
    }
    show(info, !!c.final);

    if (!playerReady) {
      show(note, true);
    } else if (!c.versions.length) {
      note.textContent = c.can_upload ? "No video yet. Use “Upload version” to add one." : "No video has been uploaded yet.";
      show(note, true);
    } else if (version && version.status !== "ready") {
      note.textContent = c.versions.every(function (v) { return v.status === "failed"; })
        ? "Video conversion failed. Please upload the video again, or ask your team to upload a new version."
        : "The video is being prepared. Interrupted conversions are retried automatically; the player appears when it is ready.";
      show(note, true);
    } else {
      show(note, false);
    }
    if (c.key === activeKey && version && version.status === "ready") loadFrame(c, version);
    else unloadFrame(c.key);
  }

  function showChannel(key) {
    if (activeKey && activeKey !== key) unloadFrame(activeKey);
    activeKey = key;
    panels.forEach(function (p) { show(p, p.dataset.videoChannel === key); });
    tabs.forEach(function (t) {
      var on = t.dataset.videoTab === key;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.classList.toggle("border-orange-500", on);
      t.classList.toggle("bg-orange-50", on);
      t.classList.toggle("text-orange-800", on);
      t.classList.toggle("border-stone-300", !on);
      t.classList.toggle("bg-white", !on);
      t.classList.toggle("text-stone-700", !on);
    });
    var c = card(key);
    if (c) render(c);
  }

  function finalError(panel, text) {
    var box = panel.querySelector("[data-video-final-error]");
    box.textContent = text || "";
    show(box, !!text);
  }

  // P1-B37: a new final version after approval sends the channel back to review (and out of the plan).
  function finalNotice(panel, text) {
    var box = panel.querySelector("[data-video-final-notice]");
    if (!box) return;
    box.textContent = text || "";
    show(box, !!text);
  }

  root.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-video-final-toggle]");
    if (!button) return;
    var panel = button.closest("[data-video-channel]");
    finalError(panel, "");
    finalNotice(panel, "");
    button.disabled = true;
    fetch(finalUrl(button.dataset.finalVersion), {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, "Content-Type": "application/json" },
      body: JSON.stringify({ final: button.dataset.mark === "1" }),
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.error || "The request failed.");
        finalNotice(panel, data.review_notice || "");
      });
    }).then(function () {
      root.dispatchEvent(new CustomEvent("jrc-video:refresh"));
    }).catch(function (error) {
      finalError(panel, error.message);
    }).then(function () { button.disabled = false; });
  });

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () { showChannel(tab.dataset.videoTab); });
  });

  async function transcriptRequest(target, method, body) {
    var row = target.closest("[data-version]");
    var section = row.querySelector("[data-transcript]");
    var error = section.querySelector("[data-transcript-error]");
    show(error, false);
    target.disabled = true;
    try {
      if (method === "DELETE") {
        body = new FormData(); body.append("delete", "1");
      }
      var response = await fetch(transcriptUrl(row.dataset.version) + (method === "GET" ? "?preview=1" : ""), {
        method: method === "DELETE" ? "POST" : method, body: body, credentials: "same-origin", headers: { "X-CSRFToken": csrf }
      });
      var data = await response.json();
      if (!response.ok || response.redirected) throw new Error(data.error || "The request failed.");
      var v = byId(card(row.closest("[data-video-channel]").dataset.videoChannel), row.dataset.version);
      var preview = section.querySelector("[data-transcript-text]");
      if (method === "GET") { preview.textContent = data.text; show(preview, true); }
      else {
        v.transcript_filename = data.filename;
        show(preview, false);
        render(card(row.closest("[data-video-channel]").dataset.videoChannel));
        root.dispatchEvent(new CustomEvent("jrc-video:refresh"));
      }
    } catch (e) { error.textContent = e.message; show(error, true); }
    finally { target.disabled = false; if (target.type === "file") target.value = ""; }
  }
  root.addEventListener("click", function (event) {
    var target = event.target.closest("button");
    if (!target) return;
    if (target.hasAttribute("data-transcript-preview")) transcriptRequest(target, "GET");
    if (target.hasAttribute("data-transcript-delete")) transcriptRequest(target, "DELETE");
  });
  root.addEventListener("change", function (event) {
    var input = event.target;
    if (!input.hasAttribute("data-transcript-file") || !input.files.length) return;
    var body = new FormData(); body.append("file", input.files[0]);
    transcriptRequest(input, "POST", body);
  });

  // Fresh state from the status feed (conversion finished, final mark changed, new version).
  root.addEventListener("jrc-video:channels", function (event) {
    channels = event.detail;
    channels.forEach(render);
  });

  if (activeKey) showChannel(activeKey);
})();
