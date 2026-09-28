/* My Garage — LASFIT-style saved vehicles.
   localStorage is the source of truth in the browser; the Flask session
   mirrors it server-side (for the header pill on first paint). */
(function () {
  "use strict";

  var KEY = "bravo_garage_v1";
  var MAX = 10;

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; }
    catch (e) { return []; }
  }
  function save(g) {
    try { localStorage.setItem(KEY, JSON.stringify(g.slice(0, MAX))); }
    catch (e) { /* private mode etc. — session still works for the visit */ }
  }
  function vkey(v) {
    return [v.year, v.make, v.model, v.trim || ""].join("|");
  }
  function label(v) {
    return v.year + " " + v.make + " " + v.model +
      (v.trim ? " " + v.trim : "");
  }
  function post(url, data) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {})
    }).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; });
    });
  }

  var modal = document.getElementById("garage-modal");
  if (!modal) return; // admin pages don't render the garage

  var listEl = document.getElementById("garage-list");
  var emptyEl = document.getElementById("garage-empty");
  var pillLabel = document.getElementById("garage-pill-label");
  var errEl = document.getElementById("garage-err");

  function updatePill() {
    if (!pillLabel) return;
    var g = load();
    if (g.length) {
      pillLabel.innerHTML = "<strong></strong><em>CHANGE</em>";
      pillLabel.querySelector("strong").textContent = label(g[0]);
    } else {
      pillLabel.innerHTML = "<strong>Select Vehicle</strong>";
    }
  }

  function render() {
    var g = load();
    listEl.innerHTML = "";
    emptyEl.hidden = g.length > 0;
    g.forEach(function (v, i) {
      var li = document.createElement("li");
      li.className = "garage-row";

      var radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "garage-main";
      radio.checked = i === 0;
      radio.setAttribute("aria-label", "Set " + label(v) + " as main vehicle");
      radio.addEventListener("change", function () { setMain(v); });
      li.appendChild(radio);

      var nm = document.createElement("span");
      nm.className = "g-vehicle";
      nm.textContent = label(v);
      li.appendChild(nm);

      if (i === 0) {
        var badge = document.createElement("span");
        badge.className = "garage-main-badge";
        badge.textContent = "MAIN";
        li.appendChild(badge);
      }

      var sp = document.createElement("span");
      sp.className = "header-spacer";
      li.appendChild(sp);

      var browse = document.createElement("button");
      browse.type = "button";
      browse.className = "garage-browse";
      browse.textContent = "BROWSE CATALOG";
      browse.addEventListener("click", function () {
        window.location.href =
          window.BravoFit.fitUrl(v.year, v.make, v.model, v.trim);
      });
      li.appendChild(browse);

      var trash = document.createElement("button");
      trash.type = "button";
      trash.className = "garage-trash";
      trash.setAttribute("aria-label", "Remove " + label(v));
      trash.textContent = "🗑";
      trash.addEventListener("click", function () { removeVehicle(v); });
      li.appendChild(trash);

      listEl.appendChild(li);
    });
    updatePill();
  }

  function setMain(v) {
    var g = load().filter(function (x) { return vkey(x) !== vkey(v); });
    g.unshift(v);
    save(g);
    post("/api/garage/main", v);
    render();
  }

  function removeVehicle(v) {
    save(load().filter(function (x) { return vkey(x) !== vkey(v); }));
    post("/api/garage/remove", v);
    render();
  }

  function openModal() {
    render();
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    var dlg = modal.querySelector(".garage-dialog");
    if (dlg) dlg.setAttribute("tabindex", "-1"), dlg.focus();
  }
  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = "";
  }

  document.getElementById("garage-open").addEventListener("click", openModal);
  modal.querySelectorAll("[data-garage-close]").forEach(function (el) {
    el.addEventListener("click", closeModal);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });

  document.getElementById("garage-clear").addEventListener("click", function () {
    save([]);
    post("/api/garage/clear", {});
    render();
  });

  var addToggle = document.getElementById("garage-add-toggle");
  var addForm = document.getElementById("garage-add-form");
  addToggle.addEventListener("click", function () {
    addForm.hidden = !addForm.hidden;
    addToggle.textContent = addForm.hidden ? "ADD VEHICLE" : "CANCEL";
    if (errEl) errEl.hidden = true;
  });

  // Populate years, then wire the shared cascade.
  fetch("/api/fitment/years").then(function (r) { return r.json(); })
    .then(function (years) {
      var ysel = document.getElementById("g-year");
      years.forEach(function (y) {
        var o = document.createElement("option");
        o.value = y; o.textContent = y;
        ysel.appendChild(o);
      });
    });

  if (window.BravoFit) {
    window.BravoFit.initFitmentFinder("garage-add-form",
      { year: "g-year", make: "g-make", model: "g-model", trim: "g-trim", go: "garage-add-go" },
      function (v) {
        errEl.hidden = true;
        post("/api/garage/add", v).then(function (res) {
          if (res.status === 200 && res.body.ok) {
            var g = load().filter(function (x) {
              return vkey(x) !== [v.year, v.make, v.model, v.trim || ""].join("|");
            });
            g.unshift({ year: parseInt(v.year, 10), make: v.make,
                        model: v.model, trim: v.trim || null });
            save(g);
            window.location.href = res.body.url;
          } else {
            errEl.textContent = "We couldn't find that vehicle — please check the year, make and model.";
            errEl.hidden = false;
          }
        });
      });
  }

  // First paint: pill + list from the browser copy, then warm the session.
  render();
  var stored = load();
  if (stored.length) {
    post("/api/garage/sync", { vehicles: stored });
  }
})();
