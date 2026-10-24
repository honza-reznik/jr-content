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

  function show(el, on, display) {
    el.classList.toggle("hidden", !on);
    if (display) el.classList.toggle(display, on);
  }
  function card(key) { return channels.filter(function (c) { return c.key === key; })[0]; }
  function panelOf(key) { return root.querySelector('[data-video-channel="' + CSS.escape(key) + '"]'); }
  function ready(c) { return c.versions.filter(function (v) { return v.status === "ready"; }); }
  function byId(c, id) { return c.versions.filter(function (v) { return v.id === id; })[0]; }

  function finalUrl(versionId) {
    return root.dataset.statusUrl.replace(/status\/$/, "versions/") + versionId + "/final/";
  }

  // The version shown: the person's choice while it still exists and is ready; until they choose,
  // always the newest ready version (so a version that has just been converted appears by itself).
  function selectedVersion(c) {
    var wanted = chosen[c.key] && byId(c, chosen[c.key]);
    if (wanted && wanted.status === "ready") return wanted;
    return ready(c)[0] || null;
  }

  function loadFrame(c, version) {
    var panel = panelOf(c.key);
    var frame = panel.querySelector("[data-video-frame]");
    var form = panel.querySelector("[data-video-sso]");
    if (!playerReady || !c.player_path || !version) return;
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

  function option(version) {
    var o = document.createElement("option");
    o.value = version.id;
    o.textContent = "v" + version.number + (version.is_final ? " · Final for publishing" : "");
    return o;
  }

  function render(c) {
    var panel = panelOf(c.key);
    if (!panel) return;
    var toolbar = panel.querySelector("[data-video-toolbar]");
    var select = panel.querySelector("[data-video-select]");
    var badge = panel.querySelector("[data-video-final-badge]");
    var toggle = panel.querySelector("[data-video-final-toggle]");
    var info = panel.querySelector("[data-video-final-info]");
    var note = panel.querySelector("[data-video-player-note]");
    var versions = ready(c);
    var version = selectedVersion(c);

    select.textContent = "";
    versions.forEach(function (v) { select.appendChild(option(v)); });
    if (version) select.value = version.id;
    show(toolbar, versions.length > 0, "flex");
    show(select.closest("label"), versions.length > 0);

    show(badge, !!(version && version.is_final), "inline-flex");
    if (c.can_mark_final && version) {
      toggle.textContent = version.is_final ? "Remove final mark" : "Mark as final for publishing";
      toggle.dataset.version = version.id;
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
    } else if (!versions.length) {
      note.textContent = "The video is being prepared. The player appears here when it is ready.";
      show(note, true);
    } else {
      show(note, false);
    }
    if (c.key === activeKey) loadFrame(c, version);
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

  root.addEventListener("change", function (event) {
    var select = event.target.closest && event.target.closest("[data-video-select]");
    if (!select) return;
    var panel = select.closest("[data-video-channel]");
    chosen[panel.dataset.videoChannel] = select.value;
    render(card(panel.dataset.videoChannel));
  });

  root.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-video-final-toggle]");
    if (!button) return;
    var panel = button.closest("[data-video-channel]");
    finalError(panel, "");
    button.disabled = true;
    fetch(finalUrl(button.dataset.version), {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, "Content-Type": "application/json" },
      body: JSON.stringify({ final: button.dataset.mark === "1" }),
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok) throw new Error(data.error || "The request failed.");
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

  // Fresh state from the status feed (conversion finished, final mark changed, new version).
  root.addEventListener("jrc-video:channels", function (event) {
    channels = event.detail;
    channels.forEach(render);
  });

  if (activeKey) showChannel(activeKey);
})();
