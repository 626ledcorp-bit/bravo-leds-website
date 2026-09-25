/* Bravo LEDs storefront interactions */
(function () {
  "use strict";

  /* ---------- Fitment finder: Year -> Make -> Model -> Trim ---------- */
  var finder = document.getElementById("fitment-finder");
  if (finder) {
    var yearSel = document.getElementById("ff-year");
    var makeSel = document.getElementById("ff-make");
    var modelSel = document.getElementById("ff-model");
    var trimSel = document.getElementById("ff-trim");
    var goBtn = document.getElementById("ff-go");

    function reset(sel, placeholder) {
      sel.innerHTML = "";
      var o = document.createElement("option");
      o.value = ""; o.textContent = placeholder;
      sel.appendChild(o);
      sel.disabled = true;
    }
    function fill(sel, items, placeholder) {
      reset(sel, placeholder);
      items.forEach(function (it) {
        var o = document.createElement("option");
        o.value = it; o.textContent = it;
        sel.appendChild(o);
      });
      sel.disabled = items.length === 0;
    }
    function get(url) {
      return fetch(url).then(function (r) { return r.json(); });
    }
    function updateGo() {
      goBtn.disabled = !(yearSel.value && makeSel.value && modelSel.value);
    }

    reset(makeSel, "Make"); reset(modelSel, "Model"); reset(trimSel, "Trim (optional)");

    yearSel.addEventListener("change", function () {
      fill(makeSel, [], "Make"); fill(modelSel, [], "Model"); fill(trimSel, [], "Trim (optional)");
      updateGo();
      if (!yearSel.value) return;
      get("/api/fitment/makes?year=" + encodeURIComponent(yearSel.value))
        .then(function (makes) { fill(makeSel, makes, "Make"); });
    });
    makeSel.addEventListener("change", function () {
      fill(modelSel, [], "Model"); fill(trimSel, [], "Trim (optional)");
      updateGo();
      if (!makeSel.value) return;
      get("/api/fitment/models?year=" + encodeURIComponent(yearSel.value) +
          "&make=" + encodeURIComponent(makeSel.value))
        .then(function (models) { fill(modelSel, models, "Model"); });
    });
    modelSel.addEventListener("change", function () {
      fill(trimSel, [], "Trim (optional)");
      updateGo();
      if (!modelSel.value) return;
      get("/api/fitment/trims?year=" + encodeURIComponent(yearSel.value) +
          "&make=" + encodeURIComponent(makeSel.value) +
          "&model=" + encodeURIComponent(modelSel.value))
        .then(function (trims) {
          if (trims.length) { fill(trimSel, trims, "Trim (optional)"); }
        });
    });
    trimSel.addEventListener("change", updateGo);

    finder.addEventListener("submit", function (e) {
      e.preventDefault();
      if (goBtn.disabled) return;
      var q = "/fitment?year=" + encodeURIComponent(yearSel.value) +
              "&make=" + encodeURIComponent(makeSel.value) +
              "&model=" + encodeURIComponent(modelSel.value);
      if (trimSel.value) q += "&trim=" + encodeURIComponent(trimSel.value);
      window.location.href = q;
    });
  }

  /* ---------- Product page: variant pills + qty ---------- */
  document.querySelectorAll("[data-pill-group]").forEach(function (group) {
    var input = document.getElementById(group.getAttribute("data-pill-group"));
    group.querySelectorAll(".pill").forEach(function (pill) {
      pill.addEventListener("click", function () {
        group.querySelectorAll(".pill").forEach(function (p) { p.classList.remove("selected"); });
        pill.classList.add("selected");
        if (input) input.value = pill.getAttribute("data-value");
      });
    });
  });

  var qtyInput = document.getElementById("qty-input");
  document.querySelectorAll("[data-qty]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (!qtyInput) return;
      var v = parseInt(qtyInput.value || "1", 10) + parseInt(btn.getAttribute("data-qty"), 10);
      qtyInput.value = Math.max(1, Math.min(99, v));
    });
  });
})();
