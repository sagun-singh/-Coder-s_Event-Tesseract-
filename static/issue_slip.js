(function () {
  "use strict";

  var printButton = document.getElementById("print-slip");
  if (printButton) {
    printButton.addEventListener("click", function () {
      window.print();
    });
  }
}());
