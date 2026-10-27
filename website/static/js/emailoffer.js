/* 10%-off email popup for BRAVO LEDS. Loaded on every storefront page.
 * Asks /api/offer/active first (admin can switch the popup off), waits
 * 20 seconds, then offers 10% off the visitor's first purchase in
 * exchange for an email. The server issues a real single-use coupon
 * code and emails it; the code is also shown on screen as a fallback.
 * Suppression (cookies): dismissed = 14 days, claimed = never again.
 */
(function () {
  "use strict";
  if (window.__emailOfferLoaded) return;
  window.__emailOfferLoaded = true;

  var path = location.pathname;
  // Never on admin, checkout, cart, payment, or the unsubscribe page.
  if (/^\/(admin|cart|checkout|pay|offer\/)/.test(path)) return;

  function getCookie(n) {
    var m = document.cookie.match(new RegExp("(?:^|; )" + n + "=([^;]*)"));
    return m ? decodeURIComponent(m[1]) : null;
  }
  function setCookie(n, v, days) {
    var d = new Date();
    d.setTime(d.getTime() + days * 864e5);
    document.cookie = n + "=" + encodeURIComponent(v) +
      "; expires=" + d.toUTCString() + "; path=/; SameSite=Lax";
  }

  if (getCookie("eo_claimed") || getCookie("eo_dismissed")) return;

  var CSS =
    ".eo-overlay{position:fixed;inset:0;background:rgba(0,0,0,.72);" +
    "z-index:9980;display:flex;align-items:center;justify-content:center;" +
    "padding:16px;opacity:0;transition:opacity .3s}" +
    ".eo-overlay.eo-show{opacity:1}" +
    ".eo-modal{background:#141416;color:#f4f4f5;border:1px solid #2e2e33;" +
    "border-radius:18px;max-width:380px;width:100%;padding:28px 22px 22px;" +
    "text-align:center;position:relative;" +
    "box-shadow:0 24px 80px rgba(0,0,0,.6)}" +
    ".eo-brand{font-size:11px;letter-spacing:3px;color:#f5a623;" +
    "font-weight:700;margin-bottom:6px}" +
    ".eo-modal h2{margin:0 0 6px;font-size:26px;color:#fff}" +
    ".eo-sub{color:#b9b9c0;font-size:14px;margin:0 0 16px;line-height:1.45}" +
    ".eo-close{position:absolute;top:10px;right:12px;background:none;" +
    "border:0;color:#8a8a93;font-size:22px;cursor:pointer;line-height:1}" +
    ".eo-close:hover{color:#fff}" +
    ".eo-form{display:flex;gap:8px}" +
    ".eo-form input{flex:1;min-width:0;padding:12px;border-radius:10px;" +
    "border:1px solid #3a3a41;background:#1d1d20;color:#fff;font-size:15px}" +
    ".eo-btn{background:#ff8c1a;color:#17130a;font-weight:800;border:0;" +
    "border-radius:10px;padding:12px 16px;cursor:pointer;font-size:15px;" +
    "white-space:nowrap}" +
    ".eo-btn[disabled]{opacity:.5;cursor:wait}" +
    ".eo-fine{color:#8a8a93;font-size:12px;margin:12px 0 0;line-height:1.4}" +
    ".eo-err{color:#ff7a72;font-size:13px;margin:10px 0 0}" +
    ".eo-code{font-size:24px;font-weight:800;letter-spacing:1px;" +
    "background:#1d1d20;border:1px dashed #f5a623;border-radius:10px;" +
    "padding:12px;margin:12px 0 6px;color:#fff}";

  function show() {
    if (getCookie("eo_claimed") || getCookie("eo_dismissed")) return;
    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var overlay = document.createElement("div");
    overlay.className = "eo-overlay";
    overlay.innerHTML =
      '<div class="eo-modal" role="dialog" aria-modal="true"' +
      ' aria-label="Get 10 percent off your first purchase">' +
      '<button class="eo-close" aria-label="Close">&times;</button>' +
      '<div class="eo-brand">BRAVO LEDS</div>' +
      "<h2>Get 10% off your first order</h2>" +
      '<p class="eo-sub">Tell us where to send it and we\'ll email you ' +
      "a single-use 10% off code.</p>" +
      '<form class="eo-form"><input type="email" required ' +
      'placeholder="you@email.com" aria-label="Email address">' +
      '<button class="eo-btn" type="submit">Email my code</button></form>' +
      '<p class="eo-err" hidden></p>' +
      '<p class="eo-fine">One code per email, single use, expires in ' +
      "30 days. Enter it in the promo code box in your cart.</p>" +
      "</div>";
    document.body.appendChild(overlay);
    requestAnimationFrame(function () { overlay.classList.add("eo-show"); });

    function dismiss() {
      setCookie("eo_dismissed", "1", 14);
      overlay.classList.remove("eo-show");
      setTimeout(function () { overlay.remove(); }, 320);
    }
    overlay.querySelector(".eo-close").addEventListener("click", dismiss);
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) dismiss();
    });

    var form = overlay.querySelector(".eo-form");
    var input = form.querySelector("input");
    var btn = form.querySelector(".eo-btn");
    var err = overlay.querySelector(".eo-err");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      btn.disabled = true;
      btn.textContent = "Sending…";
      err.hidden = true;
      fetch("/api/offer/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: input.value,
                               source: location.pathname })
      }).then(function (r) {
        return r.json().then(function (j) { return { status: r.status,
                                                      body: j }; });
      }).then(function (res) {
        if (res.status !== 200 || !res.body.ok) {
          err.textContent = (res.body && res.body.error) ||
            "Something went wrong — please try again.";
          err.hidden = false;
          btn.disabled = false;
          btn.textContent = "Email my code";
          return;
        }
        setCookie("eo_claimed", "1", 365);
        var d = res.body;
        overlay.querySelector(".eo-modal").innerHTML =
          '<button class="eo-close" aria-label="Close">&times;</button>' +
          '<div class="eo-brand">BRAVO LEDS</div>' +
          "<h2>" + (d.already ? "You're already on the list" :
                    "You're in!") + "</h2>" +
          '<p class="eo-sub">' + (d.emailed
            ? "Your code is on its way to your inbox. Here it is too:"
            : "Here is your code — save it somewhere safe:") + "</p>" +
          '<div class="eo-code">' + d.code + "</div>" +
          '<p class="eo-fine">10% off, single use' +
          (d.expires_at ? ", expires " + d.expires_at.slice(0, 10) : "") +
          ". Enter it in the promo code box in your cart.</p>";
        overlay.querySelector(".eo-close")
          .addEventListener("click", function () {
            overlay.classList.remove("eo-show");
            setTimeout(function () { overlay.remove(); }, 320);
          });
      }).catch(function () {
        err.textContent = "Something went wrong — please try again.";
        err.hidden = false;
        btn.disabled = false;
        btn.textContent = "Email my code";
      });
    });
  }

  fetch("/api/offer/active")
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d && d.active) setTimeout(show, 20000);
    })
    .catch(function () { /* popup is optional chrome; fail silent */ });
})();
