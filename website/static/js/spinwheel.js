/* Spin-to-win popup for BRAVO LEDS. Lazy-loaded on every storefront page.
 * Fetches the active promo config, waits for the trigger (delay or scroll),
 * renders a canvas wheel, and drives the spin -> email -> claim flow.
 * Server decides every outcome; the client only renders what it is told.
 */
(function () {
  "use strict";
  if (window.__spinWheelLoaded) return;
  window.__spinWheelLoaded = true;

  var PREVIEW = (function () {
    var m = /[?&]spin_preview=(\d+)/.exec(location.search);
    return m ? parseInt(m[1], 10) : null;
  })();

  function pageScope() {
    var p = location.pathname;
    if (p === "/") return "home";
    if (p.indexOf("/shop") === 0) return "shop";
    if (p.indexOf("/product/") === 0) return "product";
    return "other";
  }

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

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (r) { return r.json(); });
  }

  var CSS =
    ".spw-overlay{position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9990;" +
    "display:flex;align-items:center;justify-content:center;padding:16px;" +
    "opacity:0;transition:opacity .3s}" +
    ".spw-overlay.spw-show{opacity:1}" +
    ".spw-modal{background:#141416;color:#f4f4f5;border:1px solid #2e2e33;" +
    "border-radius:18px;max-width:380px;width:100%;padding:26px 22px 22px;" +
    "text-align:center;position:relative;box-shadow:0 24px 80px rgba(0,0,0,.6);" +
    "transform:translateY(14px) scale(.98);transition:transform .3s}" +
    ".spw-overlay.spw-show .spw-modal{transform:none}" +
    ".spw-brand{font-size:11px;letter-spacing:3px;color:#f5a623;font-weight:700;margin-bottom:6px}" +
    ".spw-modal h2{margin:0 0 6px;font-size:26px;letter-spacing:.5px;color:#fff}" +
    ".spw-sub{color:#b9b9c0;font-size:14px;margin:0 0 14px;line-height:1.45}" +
    ".spw-close{position:absolute;top:10px;right:12px;background:none;border:0;" +
    "color:#8a8a93;font-size:22px;cursor:pointer;line-height:1}" +
    ".spw-close:hover{color:#fff}" +
    ".spw-wheel-wrap{position:relative;width:280px;height:280px;margin:0 auto 14px}" +
    ".spw-pointer{position:absolute;top:-8px;left:50%;transform:translateX(-50%);" +
    "width:0;height:0;border-left:13px solid transparent;border-right:13px solid transparent;" +
    "border-top:22px solid #f5a623;z-index:2;filter:drop-shadow(0 2px 3px rgba(0,0,0,.5))}" +
    ".spw-wheel{width:280px;height:280px;border-radius:50%;" +
    "box-shadow:0 0 0 8px #f5a623,0 0 0 10px #7a520f,0 10px 40px rgba(0,0,0,.55)}" +
    ".spw-hub{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);" +
    "width:64px;height:64px;border-radius:50%;background:#f5a623;color:#141416;" +
    "display:flex;align-items:center;justify-content:center;" +
    "font-weight:800;font-size:11px;letter-spacing:1px;z-index:2;" +
    "box-shadow:0 4px 14px rgba(0,0,0,.5)}" +
    ".spw-btn{background:#f5a623;border:0;color:#141416;font-weight:800;" +
    "font-size:16px;letter-spacing:1px;padding:13px 34px;border-radius:999px;" +
    "cursor:pointer;width:100%;margin-top:4px}" +
    ".spw-btn:hover{background:#ffb63d}" +
    ".spw-btn:disabled{opacity:.55;cursor:default}" +
    ".spw-fine{font-size:11px;color:#8a8a93;margin-top:10px;line-height:1.5}" +
    ".spw-email{width:100%;box-sizing:border-box;background:#1e1e22;color:#fff;" +
    "border:1px solid #3a3a41;border-radius:10px;padding:12px 14px;font-size:15px;" +
    "margin:6px 0 10px}" +
    ".spw-email:focus{outline:none;border-color:#f5a623}" +
    ".spw-err{color:#ff8a8a;font-size:13px;min-height:18px;margin:2px 0 6px}" +
    ".spw-code{background:#1e1e22;border:1px dashed #f5a623;border-radius:10px;" +
    "font-size:24px;font-weight:800;letter-spacing:2px;color:#f5a623;" +
    "padding:14px 10px;margin:10px 0}" +
    ".spw-copy{background:#2a2a30;border:1px solid #3a3a41;color:#fff;" +
    "border-radius:8px;padding:9px 18px;font-size:13px;cursor:pointer}" +
    ".spw-copy:hover{border-color:#f5a623}" +
    ".spw-prize{font-size:17px;color:#fff;font-weight:700;margin:6px 0 2px}" +
    ".spw-exp{font-size:12px;color:#8a8a93;margin-bottom:8px}";

  function injectCSS() {
    var s = document.createElement("style");
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function brand() {
    var d = document.createElement("div");
    d.className = "spw-brand";
    d.textContent = "BRAVO LEDS";
    return d;
  }

  /* ---------- wheel drawing ---------- */
  var COLORS = ["#1c1c20", "#2b2b31"];
  function drawWheel(canvas, segments) {
    var ctx = canvas.getContext("2d");
    var n = segments.length;
    var size = canvas.width;
    var cx = size / 2, cy = size / 2, r = size / 2;
    var arc = (Math.PI * 2) / n;
    ctx.clearRect(0, 0, size, size);
    for (var i = 0; i < n; i++) {
      var a0 = i * arc - Math.PI / 2;
      var a1 = a0 + arc;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, a0, a1);
      ctx.closePath();
      ctx.fillStyle = COLORS[i % 2];
      ctx.fill();
      ctx.strokeStyle = "#3a3a41";
      ctx.lineWidth = 1;
      ctx.stroke();
      // label
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(a0 + arc / 2);
      ctx.textAlign = "right";
      ctx.fillStyle = i % 4 === 3 ? "#f5a623" : "#f4f4f5";
      ctx.font = "700 " + Math.max(11, Math.min(15, 200 / n)) + "px system-ui,sans-serif";
      var label = segments[i].label;
      if (label.length > 14) label = label.slice(0, 13) + "…";
      ctx.fillText(label, r - 12, 5);
      ctx.restore();
    }
  }

  /* Spin the wheel so segment `index` lands under the top pointer.
   * Final angle: pointer at -PI/2; segment i center at i*arc + arc/2 (in
   * wheel coords). We rotate the canvas clockwise by `rot` radians. */
  function animateSpin(canvas, segments, index, done) {
    var n = segments.length;
    var arc = (Math.PI * 2) / n;
    var segCenter = index * arc + arc / 2;
    // pointer is at angle -PI/2 in canvas coords; canvas rotation rot moves
    // segment center to: segCenter + rot (mod 2PI). Want == -PI/2 (mod 2PI).
    var target = (-Math.PI / 2 - segCenter) % (Math.PI * 2);
    if (target < 0) target += Math.PI * 2;
    var spins = 5 + Math.random() * 2;
    var total = spins * Math.PI * 2 + target;
    var dur = 4200;
    var t0 = null;
    function frame(t) {
      if (!t0) t0 = t;
      var p = Math.min(1, (t - t0) / dur);
      var e = 1 - Math.pow(1 - p, 4); // easeOutQuart
      canvas.style.transform = "rotate(" + total * e + "rad)";
      if (p < 1) requestAnimationFrame(frame);
      else done();
    }
    requestAnimationFrame(frame);
  }

  /* ---------- modal flow ---------- */
  function openModal(promo, segments, isPreview) {
    injectCSS();
    var overlay = document.createElement("div");
    overlay.className = "spw-overlay";
    overlay.innerHTML =
      '<div class="spw-modal" role="dialog" aria-modal="true">' +
      '<button class="spw-close" aria-label="Close">&times;</button>' +
      '<div class="spw-body"></div></div>';
    overlay.querySelector(".spw-modal").prepend(brand());
    document.body.appendChild(overlay);
    var body = overlay.querySelector(".spw-body");
    var closed = false;

    function close() {
      if (closed) return;
      closed = true;
      overlay.classList.remove("spw-show");
      setTimeout(function () { overlay.remove(); }, 320);
    }
    overlay.querySelector(".spw-close").onclick = close;
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) close();
    });

    function esc(s) {
      return String(s).replace(/[&<>"]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
      });
    }

    /* step 1: wheel + spin button */
    function showWheel() {
      body.innerHTML =
        "<h2>" + esc(promo.headline) + "</h2>" +
        '<p class="spw-sub">' + esc(promo.subheadline) + "</p>" +
        '<div class="spw-wheel-wrap"><div class="spw-pointer"></div>' +
        '<canvas class="spw-wheel" width="560" height="560"></canvas>' +
        '<div class="spw-hub">SPIN</div></div>' +
        '<button class="spw-btn">' + esc(promo.button_text) + "</button>" +
        '<p class="spw-fine">No purchase necessary. One spin per visitor.</p>';
      var canvas = body.querySelector(".spw-wheel");
      canvas.style.width = "280px";
      canvas.style.height = "280px";
      drawWheel(canvas, segments);
      var btn = body.querySelector(".spw-btn");
      btn.onclick = function () {
        btn.disabled = true;
        btn.textContent = "SPINNING…";
        post("/api/spin/spin", { promo_id: promo.id }).then(function (res) {
          if (!res.ok) {
            btn.disabled = false;
            btn.textContent = esc(promo.button_text);
            body.insertAdjacentHTML("beforeend",
              '<p class="spw-err">This promo just ended — sorry!</p>');
            return;
          }
          if (!isPreview) {
            setCookie("sp_spun_" + promo.id, "1", 365);
            setCookie("sp_email", "1", 365); // suppress while prize pending
          }
          animateSpin(canvas, segments, res.segment_index, function () {
            setTimeout(function () { onSpinDone(res); }, 350);
          });
        }).catch(function () {
          btn.disabled = false;
          btn.textContent = esc(promo.button_text);
        });
      };
    }

    function onSpinDone(res) {
      if (!res.won) {
        body.innerHTML =
          "<h2>" + esc(res.label) + "</h2>" +
          '<p class="spw-sub">No prize this time — but thanks for playing! ' +
          "Check back soon for more chances to win.</p>" +
          '<button class="spw-btn" id="spw-done">CLOSE</button>';
        body.querySelector("#spw-done").onclick = close;
        return;
      }
      if (res.code) {
        // email wasn't required: code issued at spin time
        showPrize(res.code, res.prize_desc, res.expires_at);
        return;
      }
      showEmailForm(res);
    }

    /* step 2: email capture */
    function showEmailForm(res) {
      body.innerHTML =
        "<h2>" + esc(res.label) + "!</h2>" +
        '<p class="spw-sub">You won <strong>' + esc(res.prize_desc) +
        "</strong>. Enter your email to reveal your personal code:</p>" +
        '<input class="spw-email" type="email" placeholder="you@email.com" ' +
        'autocomplete="email">' +
        '<p class="spw-err"></p>' +
        '<button class="spw-btn">REVEAL MY CODE</button>' +
        '<p class="spw-fine">We\'ll send deals and new products. ' +
        "Unsubscribe anytime.</p>";
      var input = body.querySelector(".spw-email");
      var err = body.querySelector(".spw-err");
      var btn = body.querySelector(".spw-btn");
      input.focus();
      function submit() {
        var email = input.value.trim();
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
          err.textContent = "Enter a valid email address.";
          return;
        }
        btn.disabled = true;
        btn.textContent = "…";
        post("/api/spin/claim", { win_id: res.win_id, email: email })
          .then(function (r2) {
            if (!r2.ok) {
              btn.disabled = false;
              btn.textContent = "REVEAL MY CODE";
              err.textContent = r2.error || "Something went wrong — try again.";
              return;
            }
            if (!isPreview) setCookie("sp_email", "1", 365);
            showPrize(r2.code, r2.prize_desc, r2.expires_at);
          })
          .catch(function () {
            btn.disabled = false;
            btn.textContent = "REVEAL MY CODE";
            err.textContent = "Network error — try again.";
          });
      }
      btn.onclick = submit;
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") submit();
      });
    }

    /* step 3: prize reveal */
    function showPrize(code, prizeDesc, expiresAt) {
      var exp = "";
      if (expiresAt) {
        try {
          exp = new Date(expiresAt + "Z").toLocaleDateString();
        } catch (e) { /* ignore */ }
      }
      body.innerHTML =
        "<h2>YOU WON!</h2>" +
        '<p class="spw-prize">' + esc(prizeDesc) + "</p>" +
        '<div class="spw-code">' + esc(code) + "</div>" +
        (exp ? '<p class="spw-exp">Use within 14 days — expires ' + esc(exp) + ".</p>"
             : '<p class="spw-exp">Apply it in your cart at checkout.</p>') +
        '<button class="spw-copy">COPY CODE</button> ' +
        '<button class="spw-btn" id="spw-shop" style="margin-top:10px">SHOP NOW</button>';
      var copyBtn = body.querySelector(".spw-copy");
      copyBtn.onclick = function () {
        function done() {
          copyBtn.textContent = "COPIED ✓";
          setTimeout(function () { copyBtn.textContent = "COPY CODE"; }, 2000);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(code).then(done, done);
        } else {
          var ta = document.createElement("textarea");
          ta.value = code;
          document.body.appendChild(ta);
          ta.select();
          try { document.execCommand("copy"); } catch (e) {}
          ta.remove();
          done();
        }
      };
      body.querySelector("#spw-shop").onclick = function () {
        location.href = "/shop";
      };
    }

    showWheel();
    requestAnimationFrame(function () {
      overlay.classList.add("spw-show");
    });
    if (!isPreview) {
      post("/api/spin/impression", { promo_id: promo.id });
      setCookie("sp_seen_" + promo.id, "1", 30);
    }
  }

  /* ---------- boot ---------- */
  function boot() {
    var url = PREVIEW
      ? "/api/spin/preview/" + PREVIEW
      : "/api/spin/active?page=" + pageScope();
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var promo = data.promo;
        if (!promo || !data.segments || data.segments.length < 2) return;
        if (!PREVIEW) {
          if (promo.once_per_visitor && getCookie("sp_seen_" + promo.id)) return;
          if (getCookie("sp_spun_" + promo.id)) return;
          if (getCookie("sp_email")) return; // already on the email list
        }
        var delayMs = Math.max(0, (promo.trigger_delay_sec || 9)) * 1000;
        var scrollPct = promo.trigger_scroll_pct || 0;
        var fired = false;
        function fire() {
          if (fired) return;
          fired = true;
          window.removeEventListener("scroll", onScroll);
          openModal(promo, data.segments, !!PREVIEW);
        }
        function onScroll() {
          var h = document.documentElement;
          var pct = (h.scrollTop / (h.scrollHeight - h.clientHeight)) * 100;
          if (pct >= scrollPct) fire();
        }
        setTimeout(fire, delayMs);
        if (scrollPct > 0 && scrollPct < 100) {
          window.addEventListener("scroll", onScroll, { passive: true });
        }
      })
      .catch(function () { /* popup is optional; never break the page */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
