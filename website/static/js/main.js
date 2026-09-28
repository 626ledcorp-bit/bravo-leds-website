/* Bravo LEDs storefront interactions */
(function () {
  "use strict";

  /* ---------- Fitment finder: Year -> Make -> Model -> Trim ----------
     Shared by the homepage hero form and the My Garage modal form. */
  function slugify(s) {
    return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }
  function fitUrl(year, make, model, trim) {
    var u = "/fit/" + encodeURIComponent(year) + "/" +
            slugify(make) + "/" + slugify(model);
    if (trim) u += "?trim=" + encodeURIComponent(trim);
    return u;
  }
  function initFitmentFinder(formId, ids, onSubmit) {
    var form = document.getElementById(formId);
    if (!form) return null;
    var yearSel = document.getElementById(ids.year);
    var makeSel = document.getElementById(ids.make);
    var modelSel = document.getElementById(ids.model);
    var trimSel = document.getElementById(ids.trim);
    var goBtn = document.getElementById(ids.go);

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

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (goBtn.disabled) return;
      onSubmit({ year: yearSel.value, make: makeSel.value,
                 model: modelSel.value, trim: trimSel.value || null });
    });
    return { form: form, updateGo: updateGo };
  }
  window.BravoFit = { initFitmentFinder: initFitmentFinder, fitUrl: fitUrl, slugify: slugify };

  // Homepage hero form -> vehicle landing page.
  initFitmentFinder("fitment-finder",
    { year: "ff-year", make: "ff-make", model: "ff-model", trim: "ff-trim", go: "ff-go" },
    function (v) { window.location.href = fitUrl(v.year, v.make, v.model, v.trim); });

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

  /* ---------- Header search toggle (mobile + desktop) ---------- */
  var searchToggle = document.getElementById("search-toggle");
  var searchDropdown = document.getElementById("search-dropdown");
  if (searchToggle && searchDropdown) {
    var searchInput = searchDropdown.querySelector('input[name="q"]');
    searchToggle.addEventListener("click", function () {
      var open = searchDropdown.classList.toggle("open");
      searchToggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open && searchInput) searchInput.focus();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && searchDropdown.classList.contains("open")) {
        searchDropdown.classList.remove("open");
        searchToggle.setAttribute("aria-expanded", "false");
        searchToggle.focus();
      }
    });
  }
})();

/* ---------- Mega-menu nav: touch toggle, Escape, aria ---------- */
(function () {
  var items = Array.prototype.slice.call(document.querySelectorAll(".nav-item"));
  function closeAll(except) {
    items.forEach(function (it) {
      if (it === except) return;
      it.classList.remove("open");
      var b = it.querySelector(".nav-btn");
      if (b) b.setAttribute("aria-expanded", "false");
    });
  }
  items.forEach(function (it) {
    var btn = it.querySelector(".nav-btn");
    if (!btn) return;
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var willOpen = !it.classList.contains("open");
      closeAll(it);
      it.classList.toggle("open", willOpen);
      btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
    });
  });
  document.addEventListener("click", function () { closeAll(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeAll();
      var t = document.getElementById("search-toggle");
      var dd = document.getElementById("search-dropdown");
      if (t && dd && dd.classList.contains("open")) {
        dd.classList.remove("open");
        t.setAttribute("aria-expanded", "false");
      }
      var dr = document.getElementById("mobile-drawer");
      var ham = document.getElementById("hamburger");
      if (dr && !dr.hidden) { dr.hidden = true; if (ham) ham.setAttribute("aria-expanded", "false"); }
    }
  });

  /* ---------- Mobile hamburger drawer ---------- */
  var ham = document.getElementById("hamburger");
  var drawer = document.getElementById("mobile-drawer");
  if (ham && drawer) {
    ham.addEventListener("click", function (e) {
      e.stopPropagation();
      var willOpen = drawer.hidden;
      drawer.hidden = !willOpen;
      ham.setAttribute("aria-expanded", willOpen ? "true" : "false");
      ham.setAttribute("aria-label", willOpen ? "Close menu" : "Open menu");
    });
  }
})();
