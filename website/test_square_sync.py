#!/usr/bin/env python3
"""Square POS inventory sync tests: config gating, catalog push with SKU
mapping/adoption, order-payment count push, webhook HMAC-SHA1 verification,
pull updates, negative clamping, conflict logging, and secret masking.

No network and no real Square credentials: square_sync._request is
monkeypatched with a fake. Secrets are scrubbed from the environment.

Run:  cd website && .venv/bin/python test_square_sync.py
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="sq_"),
                                      "store.db")
# Scrub anything that could make integrations "configured" by accident.
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "ADMIN_PASSWORD", "SMTP_HOST",
           "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
           "ORDER_NOTIFY_EMAIL", "SQUARE_ACCESS_TOKEN", "SQUARE_LOCATION_ID"):
    os.environ.pop(_v, None)

import db          # noqa: E402
import square_sync  # noqa: E402
import app as appmod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" — {extra}" if extra and not cond else ""))


# ------------------------------------------------------------ test fakes


class FakeSquare:
    """Stands in for square_sync._request. Records calls; serves canned
    responses for catalog search / batch-upsert / batch-change /
    batch-retrieve-counts / locations."""

    _instance_seq = 0

    def __init__(self):
        FakeSquare._instance_seq += 1
        # Unique id prefix per fake: real Square ids are globally unique,
        # and tests share one DB, so restarts must not collide.
        self._tag = f"F{FakeSquare._instance_seq}"
        self.calls = []          # (method, path, body)
        self.existing_skus = {}  # sku -> square variation object id
        self.counts = {}         # square object id -> int quantity
        self.next_id = 1000
        self.fail_with = None    # SquareError to raise, or None

    def _new_id(self, prefix):
        self.next_id += 1
        return f"{self._tag}-{prefix}{self.next_id}"

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.fail_with:
            raise self.fail_with
        if path.startswith("/v2/locations/"):
            return {"location": {"id": "LOC1", "name": "Test Store"}}
        if path == "/v2/catalog/search":
            kws = (((body.get("query") or {}).get("text_query") or {})
                   .get("keywords") or [])
            objs = []
            for kw in kws:
                hit = self.existing_skus.get(kw.strip().lower())
                if hit:
                    objs.append({"type": "ITEM_VARIATION", "id": hit,
                                 "item_variation_data": {"sku": kw}})
            return {"objects": objs}
        if path == "/v2/catalog/batch-upsert":
            mappings = []
            for batch in body["batches"]:
                for obj in batch["objects"]:
                    if obj["type"] == "ITEM":
                        item_id = self._new_id("ITEM")
                        mappings.append({"client_object_id": obj["id"],
                                         "object_id": item_id})
                        for var in (obj.get("item_data") or {}).get(
                                "variations", []):
                            var_id = self._new_id("VAR")
                            mappings.append(
                                {"client_object_id": var["id"],
                                 "object_id": var_id})
                            sku = ((var.get("item_variation_data") or {})
                                   .get("sku"))
                            if sku:
                                self.existing_skus[sku.strip().lower()] = var_id
                                self.counts[var_id] = 0
                    elif obj["type"] == "ITEM_VARIATION":
                        # update-in-place of an adopted/stored variation
                        mappings.append({"client_object_id": obj["id"],
                                         "object_id": obj["id"]})
            return {"objects": [], "id_mappings": mappings}
        if path == "/v2/inventory/batch-change":
            for ch in body["changes"]:
                pc = ch["physical_count"]
                self.counts[pc["catalog_object_id"]] = int(pc["quantity"])
            return {"changes": body["changes"]}
        if path == "/v2/inventory/batch-retrieve-counts":
            out = []
            for oid in body.get("catalog_object_ids", []):
                if oid in self.counts:
                    out.append({"catalog_object_id": oid, "state": "IN_STOCK",
                                "quantity": str(self.counts[oid]),
                                "location_id": "LOC1"})
            return {"counts": out}
        return {}


def use_fake():
    fake = FakeSquare()
    square_sync._request = fake
    return fake


def configure_square():
    db.set_setting("square_access_token", "sq-test-token")
    db.set_setting("square_location_id", "LOC1")
    db.set_setting("square_environment", "sandbox")


def make_product(tag, skus=None):
    if skus is None:
        skus = (f"SQ-{tag}-H11", f"SQ-{tag}-9005")
    pid = f"sqprod-{tag}"
    groups = [{"name": "Size", "values": ["H11", "9005"]}]
    db.create_product({
        "id": pid, "name": f"Bravo Test Bulbs {tag}", "category": "led-bulbs",
        "tier": "Test", "price_cents": 7000, "blurb": "test", "features": [],
        "status": "active", "variant_groups": groups,
    })
    db.sync_variations(pid, groups, overrides=[
        {"option_values": {"Size": "H11"}, "sku": skus[0],
         "inventory_qty": 10, "track_inventory": True, "_posted": True},
        {"option_values": {"Size": "9005"}, "sku": skus[1],
         "inventory_qty": 6, "track_inventory": True, "_posted": True},
    ])
    return db.get_product(pid)


def make_order(pid, vid, qty):
    p = db.get_product(pid, with_variations=False)
    unit = p["price_cents"]
    var = db.get_variation(vid)
    lines = [{"key": "k1", "product": p, "variation": var,
              "product_id": pid, "name": p["name"],
              "variation_id": vid, "variation_label": var["label"],
              "size": "", "color_temp": "", "qty": qty,
              "unit_price_cents": unit, "line_total": unit * qty}]
    oid = db.create_order(
        {"name": "Square Tester", "email": "sq@example.com",
         "line1": "1 Test St", "city": "Rosemead", "state": "CA",
         "zip": "91770"}, lines, unit * qty)
    db.mark_order_paid(oid, payment_intent_id=f"pi_sq_{oid}")
    return oid


def login(client, pw="secret"):
    return client.post("/admin/login", data={"password": pw})


def admin_client():
    os.environ["ADMIN_PASSWORD"] = "secret"
    c = appmod.app.test_client()
    login(c)
    return c


# ------------------------------------------------------------ 1. config


def test_config():
    print("1 — configuration gating")
    check("not configured with no token/location",
          square_sync.square_configured() is False)
    ok, msg = square_sync.test_connection()
    check("test_connection fails when unconfigured",
          ok is False and "token" in msg.lower())
    configure_square()
    check("configured with token + location",
          square_sync.square_configured() is True)
    check("sandbox is the default environment",
          square_sync.environment() == "sandbox")


def test_connection_ok_and_fail():
    print("2 — connection test paths")
    fake = use_fake()
    ok, msg = square_sync.test_connection()
    check("test_connection ok against fake",
          ok is True and "Test Store" in msg,
          f"ok={ok} msg={msg}")
    check("test_connection hits the location endpoint",
          any(p.startswith("/v2/locations/LOC1") for _m, p, _b in fake.calls))
    fake.fail_with = square_sync.SquareError("Square API error: nope")
    ok, msg = square_sync.test_connection()
    check("test_connection reports API failure",
          ok is False and "nope" in msg)
    check("failed test is logged",
          any(e["direction"] == "test" and e["result"] == "error"
              for e in db.list_square_sync_log(50)))
    check("token never appears in log detail",
          all("sq-test-token" not in (e["detail"] or "")
              for e in db.list_square_sync_log(50)))


# ------------------------------------------------------------ 3. catalog push


def test_push_catalog():
    print("3 — push catalog to Square (SKU mapping)")
    fake = use_fake()
    p = make_product("push")
    report = square_sync.push_catalog([p])
    check("push returns one report row per product", len(report) == 1)
    check("report action is created", report[0]["action"] == "created",
          str(report))
    ups = [c for c in fake.calls if c[1] == "/v2/catalog/batch-upsert"]
    check("batch-upsert was called", len(ups) == 1)
    body = ups[0][2]
    item = body["batches"][0]["objects"][0]
    check("upserted object is an ITEM", item["type"] == "ITEM")
    var_skus = {(v.get("item_variation_data") or {}).get("sku")
                for v in (item.get("item_data") or {}).get("variations", [])}
    check("variation SKUs travel to Square",
          var_skus == {"SQ-push-H11", "SQ-push-9005"}, str(var_skus))
    check("inventory tracking enabled on variations",
          all((v.get("item_variation_data") or {}).get("track_inventory")
              is True
              for v in (item.get("item_data") or {}).get("variations", [])))
    p2 = db.get_product(p["id"])
    check("product stores the Square ITEM id",
          bool(p2.get("square_catalog_object_id")))
    vids = {v["sku"]: v.get("square_catalog_object_id")
            for v in p2["variations"]}
    check("variations store Square object ids",
          all(vids.get(s) for s in ("SQ-push-H11", "SQ-push-9005")),
          str(vids))
    check("catalog push is logged",
          any(e["direction"] == "catalog_push"
              for e in db.list_square_sync_log(50)))
    check("public product hides the Square id",
          "square_catalog_object_id" not in db.public_product(p2))


def test_push_adopts_existing_sku():
    print("4 — push adopts existing Square SKU instead of duplicating")
    fake = use_fake()
    fake.existing_skus["sq-adoptme"] = "VAR-EXISTING"
    p = make_product("adopt", skus=("SQ-ADOPTME", "SQ-NEW1"))
    square_sync.push_catalog([p])
    vids = {v["sku"]: v.get("square_catalog_object_id")
            for v in db.get_product(p["id"])["variations"]}
    check("existing SKU variation adopts the Square id",
          vids.get("SQ-ADOPTME") == "VAR-EXISTING", str(vids))
    check("new SKU variation gets a fresh Square id",
          "VAR" in (vids.get("SQ-NEW1") or ""), str(vids))
    ups = [c for c in fake.calls if c[1] == "/v2/catalog/batch-upsert"]
    created_ids = []
    for _m, _p, body in ups:
        for batch in body["batches"]:
            for obj in batch["objects"]:
                if obj["type"] == "ITEM":
                    created_ids += [
                        (v.get("item_variation_data") or {}).get("sku")
                        for v in (obj.get("item_data") or {})
                        .get("variations", [])]
    check("adopted SKU is not re-created as a new variation",
          "SQ-ADOPTME" not in created_ids, str(created_ids))


def test_push_not_configured():
    print("5 — push fails closed without config")
    db.set_setting("square_access_token", "")
    db.set_setting("square_location_id", "")
    try:
        square_sync.push_catalog([])
        check("push_catalog raises when unconfigured", False)
    except square_sync.SquareError:
        check("push_catalog raises when unconfigured", True)
    configure_square()


# ------------------------------------------------------------ 6. order push


def test_order_payment_push():
    print("6 — website order payment pushes counts to Square")
    fake = use_fake()
    p = make_product("orderpush")
    square_sync.push_catalog([p])
    v = [x for x in db.get_product(p["id"])["variations"]
         if x["sku"] == "SQ-orderpush-H11"][0]
    sq_id = v["square_catalog_object_id"]
    db.set_setting("square_auto_sync", "1")
    oid = make_order(p["id"], v["id"], 3)
    db.decrement_stock_for_order(oid)  # mirrors the webhook flow
    check("order stock decremented locally",
          db.get_variation(v["id"])["inventory_qty"] == 7)
    n = square_sync.sync_order_to_square(oid)
    check("sync pushes one count per linked line", n == 1, f"n={n}")
    ch = [c for c in fake.calls
          if c[1] == "/v2/inventory/batch-change"]
    check("batch-change was called", len(ch) == 1)
    pc = ch[0][2]["changes"][0]["physical_count"]
    check("push targets the linked Square object",
          pc["catalog_object_id"] == sq_id)
    check("push sends the POST-decrement count as a string",
          pc["quantity"] == "7", str(pc))
    check("push carries our location id", pc["location_id"] == "LOC1")
    check("push is logged",
          any(e["direction"] == "push" and e["new_qty"] == 7
              for e in db.list_square_sync_log(50)))
    db.set_setting("square_auto_sync", "0")


def test_order_push_respects_toggle_and_unlinked():
    print("7 — auto-sync toggle + unlinked variations")
    fake = use_fake()
    p = make_product("nopush")  # never pushed to Square: no object ids
    v = [x for x in db.get_product(p["id"])["variations"]
         if x["sku"] == "SQ-nopush-H11"][0]
    oid = make_order(p["id"], v["id"], 1)
    db.decrement_stock_for_order(oid)
    n = square_sync.sync_order_to_square(oid)
    check("toggle off -> nothing pushed", n == 0)
    check("no HTTP call when toggle off",
          not any(c[1] == "/v2/inventory/batch-change"
                  for c in fake.calls))
    db.set_setting("square_auto_sync", "1")
    n = square_sync.sync_order_to_square(oid)
    check("unlinked variation -> nothing pushed", n == 0)
    db.set_setting("square_auto_sync", "0")


# ------------------------------------------------------------ 8. pull


def test_pull():
    print("8 — pull counts from Square (last-write-wins)")
    fake = use_fake()
    p = make_product("pull")
    square_sync.push_catalog([p])
    v = [x for x in db.get_product(p["id"])["variations"]
         if x["sku"] == "SQ-pull-H11"][0]
    sq_id = v["square_catalog_object_id"]
    fake.counts[sq_id] = 42
    report = square_sync.pull_inventory_counts()
    check("pull applies the Square count",
          db.get_variation(v["id"])["inventory_qty"] == 42)
    row = next((r for r in report if r["square_object_id"] == sq_id), None)
    check("pull report carries old -> new",
          row and row["old_qty"] == 10 and row["new_qty"] == 42,
          str(row))
    check("pull is logged with old/new",
          any(e["direction"] == "pull" and e["old_qty"] == 10
              and e["new_qty"] == 42
              for e in db.list_square_sync_log(100)))


def test_pull_unknown_object_skipped():
    print("9 — pull skips unlinked Square objects")
    fake = use_fake()
    fake.counts["VAR-ORPHAN"] = 9
    report = square_sync.pull_inventory_counts(["VAR-ORPHAN"])
    check("orphan object reported as skipped",
          report and report[0]["result"] == "skipped", str(report))
    check("skip is logged",
          any(e["direction"] == "pull" and e["result"] == "skipped"
              for e in db.list_square_sync_log(50)))


def test_negative_clamp():
    print("10 — sync never drives stock negative")
    p = make_product("clamp")
    v = [x for x in db.get_product(p["id"])["variations"]
         if x["sku"] == "SQ-clamp-H11"][0]
    stored = db.apply_square_count("variation", v["id"], -5, "VAR-X")
    check("negative incoming count clamps to 0", stored == 0)
    check("local stock is 0, not negative",
          db.get_variation(v["id"])["inventory_qty"] == 0)
    check("clamp is flagged in the log",
          any(e["result"] == "clamped" and e["new_qty"] == 0
              for e in db.list_square_sync_log(50)))


# ------------------------------------------------------------ 11. webhook


def _sign(body, key, url):
    digest = hmac.new(key.encode(), url.encode() + body, hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_webhook_signature_unit():
    print("11 — webhook HMAC-SHA1 verification (unit)")
    db.set_setting("square_webhook_signature_key", "whsec_unit_123")
    body = b'{"type":"inventory.count.updated"}'
    url = "https://example.com/webhooks/square"
    good = _sign(body, "whsec_unit_123", url)
    check("valid signature verifies",
          square_sync.verify_webhook_signature(body, good, url) is True)
    check("forged signature raises",
          _raises(lambda: square_sync.verify_webhook_signature(
              body, "bogus", url)))
    check("tampered body raises",
          _raises(lambda: square_sync.verify_webhook_signature(
              b'{"type":"x"}', good, url)))
    db.set_setting("square_webhook_signature_key", "")
    check("missing key fails closed",
          _raises(lambda: square_sync.verify_webhook_signature(
              body, good, url)))


def _raises(fn):
    try:
        fn()
    except square_sync.SquareError:
        return True
    except Exception:
        return False
    return False


def test_webhook_endpoint():
    print("12 — /webhooks/square end to end")
    use_fake()
    p = make_product("webhook")
    v = [x for x in db.get_product(p["id"])["variations"]
         if x["sku"] == "SQ-webhook-H11"][0]
    db.set_variation_square_id(v["id"], "VAR-WH1")
    db.set_setting("square_webhook_signature_key", "whsec_e2e_456")
    client = appmod.app.test_client()
    url = "http://localhost/webhooks/square"
    payload = {"merchant_id": "M1", "type": "inventory.count.updated",
               "event_id": "E1", "created_at": "2026-09-25T00:00:00Z",
               "data": {"type": "inventory_counts", "id": "D1",
                        "object": {"inventory_counts": [
                            {"catalog_object_id": "VAR-WH1",
                             "catalog_object_type": "ITEM_VARIATION",
                             "state": "IN_STOCK", "location_id": "LOC1",
                             "quantity": "25"}]}}}
    raw = json.dumps(payload).encode()
    # The live route signs over request.url; sign with the same URL the
    # test client uses.
    with appmod.app.test_request_context("/webhooks/square",
                                         base_url="http://localhost"):
        from flask import request as _rq
        signed_url = _rq.url
    sig = _sign(raw, "whsec_e2e_456", signed_url)
    r = client.post("/webhooks/square", data=raw,
                    headers={"x-square-hmacsha1-signature": sig,
                             "Content-Type": "application/json"})
    check("valid webhook -> 200", r.status_code == 200, str(r.status_code))
    check("webhook updates website stock",
          db.get_variation(v["id"])["inventory_qty"] == 25)
    check("webhook application is logged",
          any(e["direction"] == "pull" and e["new_qty"] == 25
              for e in db.list_square_sync_log(50)))
    r = client.post("/webhooks/square", data=raw,
                    headers={"x-square-hmacsha1-signature": "forged",
                             "Content-Type": "application/json"})
    check("forged webhook -> 400", r.status_code == 400)
    check("forged webhook leaves stock alone",
          db.get_variation(v["id"])["inventory_qty"] == 25)
    db.set_setting("square_webhook_signature_key", "")
    r = client.post("/webhooks/square", data=raw,
                    headers={"x-square-hmacsha1-signature": sig,
                             "Content-Type": "application/json"})
    check("no signature key configured -> 400", r.status_code == 400)


# ------------------------------------------------------------ 13. admin UI


def test_admin_ui_and_masking():
    print("13 — admin UI, secret masking, routes")
    c = admin_client()
    configure_square()
    db.set_setting("square_webhook_signature_key", "whsec_mask_789")
    r = c.get("/admin/settings")
    html = r.get_data(as_text=True)
    check("settings page renders", r.status_code == 200)
    check("raw access token never rendered",
          "sq-test-token" not in html)
    check("raw webhook key never rendered", "whsec_mask_789" not in html)
    check("configured indicator shown", "configured" in html)
    # Blank secret fields on POST keep the saved values.
    r = c.post("/admin/settings", data={
        "square_access_token": "", "square_location_id": "LOC1",
        "square_environment": "sandbox", "square_webhook_signature_key": "",
        "shipping_tare_oz": "3"})
    check("settings POST redirects", r.status_code == 302)
    check("blank token field keeps the saved token",
          db.get_setting("square_access_token") == "sq-test-token")
    check("blank webhook key field keeps the saved key",
          db.get_setting("square_webhook_signature_key") == "whsec_mask_789")
    # New token values are saved.
    c.post("/admin/settings", data={
        "square_access_token": "sq-rotated-1", "square_location_id": "LOC1",
        "square_environment": "production",
        "square_webhook_signature_key": "", "shipping_tare_oz": "3"})
    check("new token saved",
          db.get_setting("square_access_token") == "sq-rotated-1")
    check("environment saved",
          db.get_setting("square_environment") == "production")
    check("auto-sync toggle off by default",
          db.get_setting("square_auto_sync") == "0")
    configure_square()  # restore sandbox token for later tests
    r = c.get("/admin/square")
    check("/admin/square renders", r.status_code == 200)
    html = r.get_data(as_text=True)
    check("square page shows link status",
          "linked" in html.lower())
    check("square page shows the sync log", "Sync log" in html)
    check("square page never leaks the token", "sq-test-token" not in html)
    # Per-product push/pull buttons exist and work (fake).
    use_fake()
    p = make_product("adminpush")
    r = c.post(f"/admin/square/product/{p['id']}/push")
    check("per-product push redirects", r.status_code == 302)
    check("per-product push links variations",
          all(x.get("square_catalog_object_id")
              for x in db.get_product(p["id"])["variations"]))
    r = c.post(f"/admin/square/product/{p['id']}/pull")
    check("per-product pull redirects", r.status_code == 302)
    # Full push + pull routes.
    r = c.post("/admin/square/push-catalog")
    check("full catalog push redirects", r.status_code == 302)
    r = c.post("/admin/square/pull")
    check("full pull redirects", r.status_code == 302)
    r = c.post("/admin/square/test")
    check("test-connection route redirects", r.status_code == 302)


def main():
    db.init_db()
    tests = [test_config, test_connection_ok_and_fail, test_push_catalog,
             test_push_adopts_existing_sku, test_push_not_configured,
             test_order_payment_push,
             test_order_push_respects_toggle_and_unlinked,
             test_pull, test_pull_unknown_object_skipped, test_negative_clamp,
             test_webhook_signature_unit, test_webhook_endpoint,
             test_admin_ui_and_masking]
    for i, t in enumerate(tests, 1):
        try:
            t()
        except Exception as exc:  # noqa: BLE001 - keep the suite running
            FAIL.append(f"{t.__name__} raised")
            print(f"  [FAIL] {t.__name__} raised {exc!r}")
    print(f"\n{PASS.__len__()} passed, {FAIL.__len__()} failed"
          f" ({PASS.__len__() + FAIL.__len__()} checks)")
    if FAIL:
        print("FAILURES:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
