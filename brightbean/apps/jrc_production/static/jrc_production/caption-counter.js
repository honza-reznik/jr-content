// Živé počítadlo znaků popisku (karta Caption). Statický soubor, aby fungoval s vynucenou CSP.
// Počítání odpovídá serveru: každý znak = 1, znaky z data-escaped-chars (nové řádky apod.) navíc dle kanálu.
(function () {
  function init() {
    document.querySelectorAll("[data-caption-counter]").forEach(function (field) {
      var form = field.closest("form");
      var count = form && form.querySelector("[data-caption-count]");
      if (!count) return;
      var limit = Number(field.dataset.limit);
      var escaped = new Set(Array.from(field.dataset.escapedChars || ""));
      var update = function () {
        var length = Array.from(field.value).reduce(function (total, character) {
          return total + 1 + Number(escaped.has(character));
        }, 0);
        count.textContent = String(length);
        count.classList.toggle("font-semibold", length > limit);
        count.classList.toggle("text-red-700", length > limit);
      };
      field.addEventListener("input", update);
      update();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
