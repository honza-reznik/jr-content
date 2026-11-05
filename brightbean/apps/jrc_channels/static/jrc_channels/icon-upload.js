// Confirm selection before the explicit upload; server validation remains authoritative.
(function () {
  "use strict";
  document.querySelectorAll("[data-channel-icon-form]").forEach(function (form) {
    var input = form.querySelector('input[type="file"]');
    var status = form.querySelector("[data-icon-status]");
    var button = form.querySelector('button[type="submit"]');
    input.addEventListener("change", function () {
      var file = input.files[0];
      input.setCustomValidity("");
      button.disabled = false;
      if (!file) { status.textContent = ""; return; }
      if (file.size > 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
        status.textContent = "Choose a PNG, JPG or WebP image up to 1 MB.";
        input.setCustomValidity(status.textContent);
        button.disabled = true;
        return;
      }
      status.textContent = file.name + " selected. Click Upload icon to save it.";
    });
    form.addEventListener("submit", function () {
      status.textContent = "Uploading icon…";
      button.disabled = true;
    });
  });
})();
