/*
 * static/issue_form.js
 * Handles asset-selector switching on the issue form.
 * Served from /static so it complies with CSP default-src 'self'.
 * No inline handlers, no eval, no external requests (other than to our own
 * /coordinator/assets_for_item/ endpoint which is @coordinator_required).
 */

(function () {
  "use strict";

  var itemSel  = document.getElementById("item_id");
  var qtyRow   = document.getElementById("qty-row");
  var assetRow = document.getElementById("asset-row");
  var assetSel = document.getElementById("asset_id");
  var qtyInput = document.getElementById("qty");

  if (!itemSel) { return; }   // not on the issue form page

  itemSel.addEventListener("change", function () {
    var opt  = this.options[this.selectedIndex];
    var kind = opt.dataset ? opt.dataset.kind : opt.getAttribute("data-kind");

    if (kind === "asset") {
      qtyRow.classList.add("js-hidden");
      assetRow.classList.remove("js-hidden");
      qtyInput.value = "1";

      var itemId = opt.value;
      var url = "/coordinator/assets_for_item/" + encodeURIComponent(itemId);

      fetch(url, { credentials: "same-origin" })
        .then(function (resp) {
          if (!resp.ok) { throw new Error("Network error"); }
          return resp.json();
        })
        .then(function (data) {
          assetSel.innerHTML = "<option value=\"\">-- Select asset --<\/option>";
          data.forEach(function (a) {
            var o = document.createElement("option");
            o.value       = a.asset_id;
            o.textContent = a.serial_no + " (" + a.condition + ")";
            assetSel.appendChild(o);
          });
        })
        .catch(function () {
          assetSel.innerHTML = "<option value=\"\">Error loading assets<\/option>";
        });
    } else {
      qtyRow.classList.remove("js-hidden");
      assetRow.classList.add("js-hidden");
      assetSel.value = "";
    }
  });
}());
