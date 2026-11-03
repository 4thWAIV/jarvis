// The deck's own behaviour: a tea's card opens its detail view, and the
// view's Close (or Escape) shuts it. The story helper drives these by
// clicking them, as a person would.
(function () {
  "use strict";
  function close() {
    [].slice.call(document.querySelectorAll(".detail")).forEach(function (d) { d.hidden = true; });
  }
  [].slice.call(document.querySelectorAll(".tea")).forEach(function (card) {
    card.addEventListener("click", function () {
      close();
      document.getElementById("detail-" + card.dataset.detail).hidden = false;
    });
  });
  [].slice.call(document.querySelectorAll(".detail .close")).forEach(function (b) {
    b.addEventListener("click", close);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
})();
