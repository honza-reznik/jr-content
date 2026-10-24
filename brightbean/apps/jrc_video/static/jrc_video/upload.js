// Karta Video: nahrání verze videa po částech přímo do úložiště (F5-B05).
// Statický soubor kvůli vynucené CSP. Prohlížeč nikdy nedostane token FreeFrame:
// serverové adresy vrací Brightbean a podepsané adresy částí jsou krátké (<= 15 min).
(function () {
  "use strict";
  var root = document.getElementById("jrc-video");
  if (!root) return;

  var PARALLEL = 3;
  var ATTEMPTS = 5;
  var POLL_MS = 4000;
  var csrf = root.dataset.csrf;
  var maxBytes = Number(root.dataset.maxBytes) || 0;
  var notice = root.querySelector("[data-video-notice]");
  var active = null; // upload in progress in this tab: { channel, version, aborted }
  var pollTimer = null;

  function show(el, on) { el.classList.toggle("hidden", !on); }
  function sleep(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  function setNotice(text) {
    notice.textContent = text || "";
    notice.className = "mb-4 rounded-lg px-3 py-2 text-sm bg-emerald-50 text-emerald-800" + (text ? "" : " hidden");
  }

  function api(method, url, body) {
    var options = { method: method, credentials: "same-origin", headers: { "X-CSRFToken": csrf } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || response.redirected) {
          var error = new Error(data.error || "The request failed.");
          error.status = response.status;
          throw error;
        }
        return data;
      });
    });
  }

  function versionUrl(id, action) {
    return root.dataset.statusUrl.replace(/status\/$/, "versions/") + id + "/" + action + "/";
  }

  // ----- rendering of the version list from the server feed -----

  function badge(status) {
    var colors = status === "ready" ? "bg-emerald-100 text-emerald-800"
      : status === "failed" ? "bg-red-100 text-red-800" : "bg-amber-100 text-amber-800";
    return "rounded-full px-2.5 py-1 text-xs font-medium " + colors;
  }

  function renderRow(card, v) {
    var li = document.createElement("li");
    li.className = "flex flex-col gap-1 rounded-lg bg-stone-50 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between";
    li.dataset.version = v.id; li.dataset.status = v.status; li.dataset.mine = v.mine ? "1" : "0";
    li.dataset.name = v.filename; li.dataset.size = String(v.size_bytes);
    var left = document.createElement("div"); left.className = "min-w-0";
    var title = document.createElement("p"); title.className = "text-sm font-medium text-stone-800";
    title.textContent = "v" + v.number + " ";
    var name = document.createElement("span"); name.className = "font-normal text-stone-500";
    name.textContent = "· " + v.filename; title.appendChild(name);
    if (v.is_final) {
      var mark = document.createElement("span"); mark.setAttribute("data-video-final-mark", "");
      mark.className = "ml-1 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800";
      mark.textContent = "Final for publishing"; title.appendChild(document.createTextNode(" ")); title.appendChild(mark);
    }
    var meta = document.createElement("p"); meta.className = "text-xs text-stone-500";
    meta.textContent = [v.uploaded_by, v.uploaded_at, v.duration, v.resolution].filter(Boolean).join(" · ");
    left.appendChild(title); left.appendChild(meta);
    var right = document.createElement("div"); right.className = "flex items-center gap-2";
    var canResume = v.mine && v.status === "uploading" && card.dataset.canUpload === "1";
    if (canResume && !(active && active.version === v.id)) {
      right.appendChild(rowButton("Continue upload", "data-video-resume", "border-orange-300 text-orange-700 hover:bg-orange-50 font-semibold"));
      right.appendChild(rowButton("Discard", "data-video-discard-row", "border-stone-300 text-stone-700 hover:bg-stone-50 font-medium"));
    }
    var status = document.createElement("span"); status.setAttribute("data-video-status", "");
    status.className = badge(v.status); status.textContent = v.status_label;
    right.appendChild(status);
    li.appendChild(left); li.appendChild(right);
    return li;
  }

  function rowButton(label, attr, classes) {
    var b = document.createElement("button"); b.type = "button"; b.setAttribute(attr, "");
    b.className = "rounded-lg border bg-white px-3 py-1.5 text-xs " + classes; b.textContent = label;
    return b;
  }

  function render(channels) {
    channels.forEach(function (data) {
      var card = root.querySelector('[data-video-channel="' + CSS.escape(data.key) + '"]');
      if (!card) return;
      var list = card.querySelector("[data-video-versions]");
      list.textContent = "";
      if (!data.versions.length) {
        var empty = document.createElement("li"); empty.setAttribute("data-video-empty", "");
        empty.className = "rounded-lg border border-dashed border-stone-300 px-4 py-6 text-center text-sm text-stone-500";
        empty.textContent = "No video yet."; list.appendChild(empty);
      }
      data.versions.forEach(function (v) { list.appendChild(renderRow(card, v)); });
    });
    // The player toolbar (video-tab.js) follows the same feed.
    root.dispatchEvent(new CustomEvent("jrc-video:channels", { detail: channels }));
  }

  function needsPolling() {
    return !!root.querySelector('[data-status="processing"], [data-status="uploading"]:not([data-mine="1"])') ||
      !!active;
  }

  function refresh() {
    return api("GET", root.dataset.statusUrl).then(function (data) {
      render(data.channels);
      return data;
    }).catch(function () { /* keep the last known state */ });
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (!needsPolling()) return;
    pollTimer = setTimeout(function () { refresh().then(schedulePoll); }, POLL_MS);
  }

  // ----- upload -----

  function cardOf(el) { return el.closest("[data-video-channel]"); }
  function errorBox(card) { return card.querySelector("[data-video-error]"); }

  function fail(card, message) {
    var box = errorBox(card); box.textContent = message; show(box, true);
  }

  function clearError(card) { show(errorBox(card), false); }

  function setProgress(card, label, fraction, discardable) {
    var panel = card.querySelector("[data-video-progress]");
    show(panel, true);
    card.querySelector("[data-video-progress-label]").textContent = label;
    var pct = Math.max(0, Math.min(100, Math.round(fraction * 100)));
    card.querySelector("[data-video-bar]").style.width = pct + "%";
    card.querySelector('[role="progressbar"]').setAttribute("aria-valuenow", String(pct));
    show(card.querySelector("[data-video-discard]"), !!discardable);
  }

  function hideProgress(card) { show(card.querySelector("[data-video-progress]"), false); }

  function putPart(url, blob, onBytes, state) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      state.xhr.add(xhr);
      xhr.open("PUT", url);
      xhr.upload.onprogress = function (event) { onBytes(event.loaded); };
      xhr.onload = function () {
        state.xhr.delete(xhr);
        if (xhr.status >= 200 && xhr.status < 300) return resolve();
        var error = new Error("Storage answered " + xhr.status); error.storage = true; reject(error);
      };
      xhr.onerror = function () { state.xhr.delete(xhr); var e = new Error("Connection problem."); e.storage = true; reject(e); };
      xhr.onabort = function () { state.xhr.delete(xhr); reject(new Error("aborted")); };
      xhr.send(blob);
    });
  }

  async function sendPart(file, info, number, state, onBytes) {
    var start = (number - 1) * info.chunk_size;
    var blob = file.slice(start, Math.min(start + info.chunk_size, file.size));
    var lastError;
    for (var attempt = 0; attempt < ATTEMPTS; attempt++) {
      if (state.aborted) throw new Error("aborted");
      try {
        // A fresh signed address for every attempt: they are short-lived.
        var signed = await api("POST", versionUrl(info.version, "part"),
          { part: number, s3_key: info.s3_key, upload_id: info.upload_id });
        await putPart(signed.url, blob, onBytes, state);
        return;
      } catch (error) {
        lastError = error;
        onBytes(0);
        // Storage trouble (including an expired address) is retried with a new address; a refusal
        // from the application (permission, wrong upload) will not heal by retrying.
        if (!error.storage && error.status && error.status !== 502 && error.status !== 503) throw error;
        if (error.message === "aborted") throw error;
        await sleep(Math.min(8000, 800 * Math.pow(2, attempt)));
      }
    }
    throw lastError;
  }

  async function transfer(card, file, info) {
    var state = active;
    var total = Math.ceil(file.size / info.chunk_size);
    var held = new Set(info.held_parts);
    var pending = [];
    for (var n = 1; n <= total; n++) if (!held.has(n)) pending.push(n);
    var inFlight = {};
    var done = 0;
    function sizeOf(number) {
      return Math.min(info.chunk_size, file.size - (number - 1) * info.chunk_size);
    }
    var heldBytes = 0;
    held.forEach(function (number) { if (number >= 1 && number <= total) heldBytes += sizeOf(number); });
    function update() {
      var sent = heldBytes + done;
      Object.keys(inFlight).forEach(function (k) { sent += inFlight[k]; });
      var fraction = file.size ? sent / file.size : 1;
      setProgress(card, (held.size ? "Continuing " : "Uploading ") + file.name + " · " + Math.round(fraction * 100) + "%", fraction, true);
    }
    update();
    var cursor = 0;
    async function worker() {
      while (cursor < pending.length) {
        var number = pending[cursor++];
        await sendPart(file, info, number, state, function (loaded) { inFlight[number] = loaded; update(); });
        delete inFlight[number];
        done += sizeOf(number);
        update();
      }
    }
    var workers = [];
    for (var i = 0; i < Math.min(PARALLEL, pending.length); i++) workers.push(worker());
    await Promise.all(workers);
  }

  async function finish(card, info) {
    setProgress(card, "Finishing upload…", 1, false);
    var lastError;
    for (var attempt = 0; attempt < 4; attempt++) {
      try {
        await api("POST", versionUrl(info.version, "complete"), { s3_key: info.s3_key, upload_id: info.upload_id });
        return;
      } catch (error) {
        lastError = error;
        if (error.status && error.status !== 503) throw error;
        await sleep(2000 * (attempt + 1));
      }
    }
    throw lastError;
  }

  async function run(card, file, info) {
    if (active) { fail(card, "Another upload is running in this tab."); return; }
    active = { channel: card.dataset.videoChannel, version: info.version, aborted: false, xhr: new Set(), info: info };
    window.addEventListener("beforeunload", warnLeave);
    try {
      await refresh(); // shows the new version; its own row has no buttons while this tab uploads it
      await transfer(card, file, info);
      await finish(card, info);
      hideProgress(card);
      setNotice("Upload finished. The video is being converted.");
    } catch (error) {
      if (active.aborted) { hideProgress(card); }
      else {
        hideProgress(card);
        fail(card, (error.status ? error.message : "The connection was interrupted.") +
          " Your progress is saved: choose “Continue upload” to send only what is missing.");
      }
    } finally {
      active = null;
      window.removeEventListener("beforeunload", warnLeave);
      await refresh();
      schedulePoll();
    }
  }

  function warnLeave(event) { event.preventDefault(); event.returnValue = ""; }

  function validate(card, file) {
    var extension = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    var allowed = card.querySelector("[data-video-file]").accept.split(",");
    if (allowed.indexOf(extension) === -1) { fail(card, "Unsupported file type. Use " + allowed.join(", ") + "."); return false; }
    if (!file.size) { fail(card, "The file is empty."); return false; }
    if (maxBytes && file.size > maxBytes) { fail(card, "The file is larger than " + Math.floor(maxBytes / 1073741824) + " GB."); return false; }
    return true;
  }

  async function startNew(card, file) {
    clearError(card); setNotice("");
    if (!validate(card, file)) return;
    try {
      setProgress(card, "Preparing…", 0, false);
      var info = await api("POST", root.dataset.startUrl,
        { channel: card.dataset.videoChannel, filename: file.name, size: file.size, type: file.type });
      await run(card, file, info);
    } catch (error) {
      hideProgress(card); fail(card, error.message);
      await refresh();
    }
  }

  async function resumeExisting(card, versionId, file) {
    clearError(card); setNotice("");
    var row = card.querySelector('[data-version="' + CSS.escape(versionId) + '"]');
    if (!validate(card, file)) return;
    if (row && (row.dataset.name !== file.name || row.dataset.size !== String(file.size))) {
      fail(card, "Choose the same file as before: " + row.dataset.name + ".");
      return;
    }
    try {
      setProgress(card, "Checking what is already uploaded…", 0, false);
      var info = await api("GET", versionUrl(versionId, "resume"));
      if (info.size !== file.size) { hideProgress(card); fail(card, "Choose the same file as before."); return; }
      if (info.state === "assembled") {
        active = { channel: card.dataset.videoChannel, version: info.version, aborted: false, xhr: new Set(), info: info };
        try { await finish(card, info); hideProgress(card); setNotice("Upload finished. The video is being converted."); }
        finally { active = null; await refresh(); schedulePoll(); }
        return;
      }
      await run(card, file, info);
    } catch (error) {
      hideProgress(card); fail(card, error.message); await refresh();
    }
  }

  async function discard(card, versionId) {
    clearError(card);
    try {
      var info = await api("GET", versionUrl(versionId, "resume"));
      await api("POST", versionUrl(versionId, "abort"), { s3_key: info.s3_key, upload_id: info.upload_id });
      setNotice("Upload discarded.");
    } catch (error) {
      fail(card, error.message);
    }
    await refresh();
  }

  // ----- events -----

  var resumeTarget = null;
  root.addEventListener("click", function (event) {
    var target = event.target.closest("button");
    if (!target || !root.contains(target)) return;
    var card = cardOf(target);
    if (target.hasAttribute("data-video-upload")) {
      resumeTarget = null; card.querySelector("[data-video-file]").click();
    } else if (target.hasAttribute("data-video-resume")) {
      resumeTarget = target.closest("[data-version]").dataset.version;
      card.querySelector("[data-video-file]").click();
    } else if (target.hasAttribute("data-video-discard-row")) {
      discard(card, target.closest("[data-version]").dataset.version);
    } else if (target.hasAttribute("data-video-discard") && active) {
      var state = active, info = state.info;
      state.aborted = true;
      state.xhr.forEach(function (xhr) { xhr.abort(); });
      // The abort call waits for the running requests to stop, then removes the version.
      sleep(300).then(function () { return api("POST", versionUrl(info.version, "abort"), { s3_key: info.s3_key, upload_id: info.upload_id }); })
        .then(function () { setNotice("Upload discarded."); })
        .catch(function (error) { fail(card, error.message); })
        .then(refresh);
    }
  });

  root.addEventListener("change", function (event) {
    var input = event.target;
    if (!input.hasAttribute || !input.hasAttribute("data-video-file")) return;
    var file = input.files && input.files[0];
    var card = cardOf(input);
    var resumeId = resumeTarget; resumeTarget = null;
    input.value = "";
    if (!file) return;
    if (resumeId) resumeExisting(card, resumeId, file); else startNew(card, file);
  });

  // video-tab.js asks for a fresh feed after marking a final version.
  root.addEventListener("jrc-video:refresh", function () { refresh().then(schedulePoll); });

  refresh().then(schedulePoll);
  schedulePoll();
})();
