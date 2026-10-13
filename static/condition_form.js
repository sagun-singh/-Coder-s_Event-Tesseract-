/*
 * static/condition_form.js
 * Shows/hides asset-id or qty field based on the selected item kind.
 * Served from /static to comply with CSP default-src 'self'.
 */
(function () {
  "use strict";
  var sel       = document.getElementById("item_id");
  var assetRow  = document.getElementById("asset-id-row");
  var qtyRow    = document.getElementById("cond-qty-row");
  if (!sel) { return; }

  sel.addEventListener("change", function () {
    var opt  = this.options[this.selectedIndex];
    var kind = opt.dataset ? opt.dataset.kind : opt.getAttribute("data-kind");
    assetRow.classList.toggle("js-hidden", kind !== "asset");
    qtyRow.classList.toggle("js-hidden", kind !== "quantity");
  });
}());
