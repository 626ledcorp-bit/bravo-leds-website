#!/usr/bin/env python3
"""Bulk CSV import tests: template download, full product+variation import,
inventory-only import, duplicate/prohibited-word validation, all-errors-
before-write, blank-cell preservation, and the 5 MB / two-step route flow.

Run:  cd website && .venv/bin/python test_csv_import.py
"""

import io
import os
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ["STORE_DB"] = os.path.join(tempfile.mkdtemp(prefix="csvimp_"),
                                      "store.db")
os.environ["FITMENT_DIR"] = tempfile.mkdtemp(prefix="fit_")
os.environ["SEMA_DIR"] = tempfile.mkdtemp(prefix="sema_")
os.environ["ADMIN_PASSWORD"] = "test-admin-pw"
for _v in ("STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY",
           "STRIPE_WEBHOOK_SECRET", "SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
    os.environ.pop(_v, None)

import db          # noqa: E402
import csvimport   # noqa: E402
import app as appmod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}"
          + (f" -- {extra}" if extra and not cond else ""))


def admin_client():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    r = c.post("/admin/login", data={"password": "test-admin-pw"},
               follow_redirects=True)
    assert r.status_code == 200, "admin login failed"
    return c


def full_csv(rows):
    """Build a full-mode CSV string from dict rows."""
    import csv as _csv
    buf = io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=csvimport.FULL_COLUMNS,
                        extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in csvimport.FULL_COLUMNS})
    return buf.getvalue()


def inv_csv(rows):
    import csv as _csv
    buf = io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=csvimport.INVENTORY_COLUMNS,
                        extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in csvimport.INVENTORY_COLUMNS})
    return buf.getvalue()


VAR_PRODUCT = [
    {"name": "Import Test LED Bulbs", "sku": "CSV-001", "category": "led-bulbs",
     "price": "49.99", "cost": "18.00", "msku": "M-CSV-001",
     "quantity": "10", "status": "draft",
     "description": "A test product.", "taxable": "yes",
     "option1_name": "Size", "option1_value": "H11",
     "variation_sku": "CSV-001-H11", "variation_quantity": "5"},
    {"name": "Import Test LED Bulbs", "sku": "CSV-001", "category": "led-bulbs",
     "price": "49.99", "quantity": "10", "status": "draft",
     "option1_name": "Size", "option1_value": "9005",
     "variation_sku": "CSV-001-9005", "variation_quantity": "7"},
]


def main():
    db.init_db()
    print("== templates ==")
    t = csvimport.template_csv("full")
    header = t.splitlines()[0]
    check("full template has every column header",
          all(c in header.split(",") for c in csvimport.FULL_COLUMNS))
    check("full template has header + 1 example row",
          len(t.strip().splitlines()) == 2)
    check("full template example row has an sku",
          "EXAMPLE-001" in t)
    t2 = csvimport.template_csv("inventory")
    check("inventory template headers",
          t2.splitlines()[0] == "sku,quantity,variation_sku")
    check("inventory template has example row",
          len(t2.strip().splitlines()) == 2)

    c = admin_client()
    print("== template routes ==")
    r = c.get("/admin/products/import/template?mode=full")
    check("template download 200", r.status_code == 200)
    check("template download is CSV",
          "text/csv" in r.headers.get("Content-Type", ""))
    check("template download is attachment",
          "attachment" in r.headers.get("Content-Disposition", ""))
    check("template download has headers",
          b"name,sku,msku" in r.data)
    r = c.get("/admin/products/import")
    check("import page loads", r.status_code == 200)
    check("import page explains blank cells",
          b"blank cell keeps the existing value" in r.data)
    check("import page links templates",
          b"import/template?mode=full" in r.data
          and b"import/template?mode=inventory" in r.data)

    print("== valid full import ==")
    res = csvimport.validate(full_csv(VAR_PRODUCT).encode(), "full")
    check("valid file passes", res["valid"], str(res["errors"]))
    check("summary counts create",
          res["summary"] == {"create": 1, "update": 0, "errors": 0},
          str(res["summary"]))
    counts = csvimport.apply(res)
    check("apply counts",
          counts == {"created": 1, "updated": 0, "variations": 2},
          str(counts))
    p = db.get_product_by_sku("CSV-001")
    check("product created with Bravo prefix",
          p is not None and p["name"] == "Bravo Import Test LED Bulbs",
          p["name"] if p else None)
    check("price parsed to cents", p["price_cents"] == 4999)
    check("cost stored (admin-only)", p["cost_cents"] == 1800)
    check("msku stored (admin-only)", p["msku"] == "M-CSV-001")
    check("status draft by default", p["status"] == "draft")
    check("option groups derived",
          p["variant_groups"] == [{"name": "Size",
                                   "values": ["H11", "9005"]}],
          str(p["variant_groups"]))
    check("two variations created", len(p["variations"]) == 2)
    v_by_sku = {v["sku"]: v for v in p["variations"]}
    check("variation SKUs stored",
          set(v_by_sku) == {"CSV-001-H11", "CSV-001-9005"})
    check("variation quantities set",
          v_by_sku["CSV-001-H11"]["inventory_qty"] == 5
          and v_by_sku["CSV-001-9005"]["inventory_qty"] == 7)
    check("variation option values",
          v_by_sku["CSV-001-H11"]["option_values"] == {"Size": "H11"})
    check("product stock set", db.get_stock(p["id"])["stock"] == 10)

    print("== update matched by SKU, blanks preserved ==")
    upd = [{"sku": "CSV-001", "price": "59.99", "description": "",
            "option1_name": "Size", "option1_value": "H11",
            "variation_sku": "CSV-001-H11", "variation_quantity": "12"}]
    res = csvimport.validate(full_csv(upd).encode(), "full")
    check("update file valid", res["valid"])
    check("update summary",
          res["summary"] == {"create": 0, "update": 1, "errors": 0})
    check("preview marks update action",
          res["products"][0]["action"] == "update")
    check("preview marks variation update",
          res["products"][0]["variations"][0]["action"] == "update")
    csvimport.apply(res)
    p2 = db.get_product_by_sku("CSV-001")
    check("price updated", p2["price_cents"] == 5999)
    check("blank description kept existing",
          p2["description"] == "A test product.")
    check("blank name kept existing",
          p2["name"] == "Bravo Import Test LED Bulbs")
    check("cost kept when blank", p2["cost_cents"] == 1800)
    v = db.get_variation_by_sku("CSV-001-H11")
    check("variation matched by SKU, qty updated",
          v["inventory_qty"] == 12)
    check("other variation untouched",
          db.get_variation_by_sku("CSV-001-9005")["inventory_qty"] == 7)

    print("== explicit zero quantity ==")
    res = csvimport.validate(
        full_csv([{"sku": "CSV-001", "quantity": "0"}]).encode(), "full")
    check("zero-qty file valid", res["valid"])
    csvimport.apply(res)
    check("explicit 0 sets stock to zero",
          db.get_stock(p2["id"])["stock"] == 0)

    print("== price semantics ==")
    res = csvimport.validate(full_csv(
        [{"name": "Sale Test", "sku": "CSV-SALE", "category": "fog",
          "price": "39.99", "compare_at_price": "59.99"}]).encode(), "full")
    check("sale file valid", res["valid"])
    csvimport.apply(res)
    ps = db.get_product_by_sku("CSV-SALE")
    check("compare_at becomes regular price", ps["price_cents"] == 5999)
    check("price becomes sale price", ps["sale_price_cents"] == 3999)

    print("== inventory-only import ==")
    before = db.get_stock(p2["id"])["stock"]
    res = csvimport.validate(inv_csv([
        {"sku": "CSV-001", "quantity": "33"},
        {"sku": "CSV-001", "quantity": "9",
         "variation_sku": "CSV-001-9005"},
    ]).encode(), "inventory")
    check("inventory file valid", res["valid"], str(res["errors"]))
    counts = csvimport.apply(res)
    check("inventory apply count", counts == {"updated": 2})
    check("product stock updated",
          db.get_stock(p2["id"])["stock"] == 33 and before != 33)
    check("variation stock updated",
          db.get_variation_by_sku("CSV-001-9005")["inventory_qty"] == 9)

    print("== duplicates ==")
    dup = [dict(VAR_PRODUCT[0]), dict(VAR_PRODUCT[1])]
    dup[1]["variation_sku"] = "CSV-001-H11"  # duplicate variation SKU
    res = csvimport.validate(full_csv(dup).encode(), "full")
    check("duplicate variation SKU rejected", not res["valid"])
    check("duplicate error message",
          any("appears twice" in e
              for v in res["products"][0]["variations"]
              for e in v["errors"]))
    n_before = len(db.list_products())
    try:
        csvimport.apply(res)
        applied = True
    except ValueError:
        applied = False
    check("apply refuses invalid result", not applied)
    check("no partial writes from invalid file",
          len(db.list_products()) == n_before)

    conflict = [dict(VAR_PRODUCT[0]), dict(VAR_PRODUCT[1])]
    conflict[0]["sku"] = conflict[1]["sku"] = "CSV-CONF"
    conflict[1]["name"] = "Totally Different Bulbs"
    res = csvimport.validate(full_csv(conflict).encode(), "full")
    check("conflicting names on one SKU rejected", not res["valid"])

    print("== prohibited words ==")
    res = csvimport.validate(full_csv(
        [{"name": "Headlight LED Bulbs", "sku": "CSV-HL",
          "category": "led-bulbs", "price": "29.99"}]).encode(), "full")
    check("'headlight' name rejected", not res["valid"])
    check("headlight error mentions the word",
          any("headlight" in e for e in res["products"][0]["errors"]))
    res = csvimport.validate(full_csv(
        [{"name": "Off-Road LED Bulbs", "sku": "CSV-OR",
          "category": "led-bulbs", "price": "29.99"}]).encode(), "full")
    check("'off-road' name rejected", not res["valid"])

    print("== misc validation ==")
    res = csvimport.validate(full_csv(
        [{"name": "Bad Cat", "sku": "CSV-BC", "category": "nope",
          "price": "10"}]).encode(), "full")
    check("unknown category rejected", not res["valid"])
    res = csvimport.validate(full_csv(
        [{"name": "Neg Price", "sku": "CSV-NP", "category": "fog",
          "price": "-5"}]).encode(), "full")
    check("negative price rejected", not res["valid"])
    res = csvimport.validate(full_csv(
        [{"name": "Zero Price", "sku": "CSV-ZP", "category": "fog",
          "price": "0"}]).encode(), "full")
    check("zero price rejected", not res["valid"])
    res = csvimport.validate(full_csv(
        [{"sku": "CSV-NONAME", "category": "fog",
          "price": "10"}]).encode(), "full")
    check("missing name on create rejected", not res["valid"])
    res = csvimport.validate(full_csv(
        [{"name": "No Price", "sku": "CSV-NOPR",
          "category": "fog"}]).encode(), "full")
    check("missing price on create rejected", not res["valid"])
    bad = full_csv([{"name": "X", "sku": "CSV-X", "category": "fog",
                      "price": "10"}])
    # extrasaction="ignore" would drop the bogus column in the helper,
    # so splice it into the header + row manually.
    lines = bad.splitlines()
    lines[0] += ",bogus_col"
    lines[1] += ",1"
    res = csvimport.validate(("\n".join(lines) + "\n").encode(), "full")
    check("unknown column rejected",
          not res["valid"]
          and any("Unknown column" in e for e in res["errors"]))
    res = csvimport.validate(inv_csv(
        [{"sku": "NOPE-SKU", "quantity": "5"}]).encode(), "inventory")
    check("unknown SKU in inventory import rejected", not res["valid"])
    res = csvimport.validate(inv_csv(
        [{"sku": "CSV-001", "quantity": "5"},
         {"sku": "CSV-001", "quantity": "6"}]).encode(), "inventory")
    check("duplicate inventory row rejected", not res["valid"])

    print("== all errors before any write ==")
    mixed = [
        {"name": "Good Row Product", "sku": "CSV-GOOD", "category": "fog",
         "price": "19.99"},
        {"name": "Bad Row Product", "sku": "CSV-BAD", "category": "fog",
         "price": "not-a-number"},
    ]
    n_before = len(db.list_products())
    res = csvimport.validate(full_csv(mixed).encode(), "full")
    check("mixed file invalid", not res["valid"])
    check("good row alone would be fine",
          res["products"][0]["action"] == "create"
          and not res["products"][0]["errors"])
    check("bad row flagged",
          res["products"][1]["errors"])
    check("nothing written for mixed file",
          len(db.list_products()) == n_before
          and db.get_product_by_sku("CSV-GOOD") is None)

    print("== route flow: upload -> preview -> confirm ==")
    data = {"mode": "full",
            "csv": (io.BytesIO(full_csv([
                {"name": "Route Flow Bulbs", "sku": "CSV-ROUTE",
                 "category": "turn", "price": "25.00",
                 "quantity": "8", "status": "active",
                 "cost": "9.99", "msku": "M-ROUTE",
                 "option1_name": "Color", "option1_value": "Amber",
                 "variation_sku": "CSV-ROUTE-A", "variation_quantity": "3"},
            ]).encode()), "flow.csv")}
    r = c.post("/admin/products/import", data=data,
               content_type="multipart/form-data")
    check("upload returns preview 200", r.status_code == 200)
    check("preview shows create",
          b"to create" in r.data or b"create" in r.data)
    check("preview has confirm button", b"Confirm import" in r.data)
    check("preview shows no writes yet",
          db.get_product_by_sku("CSV-ROUTE") is None)
    import re as _re
    m = _re.search(rb'name="token" value="([0-9a-f]{32})"', r.data)
    check("preview carries a token", bool(m))
    token = m.group(1).decode()
    r = c.post("/admin/products/import/confirm",
               data={"token": token, "mode": "full"},
               follow_redirects=False)
    check("confirm redirects", r.status_code in (301, 302, 303))
    check("confirm lands on admin products",
          "/admin" in r.headers.get("Location", ""))
    pr = db.get_product_by_sku("CSV-ROUTE")
    check("confirm wrote the product", pr is not None)
    check("confirm wrote the variation",
          db.get_variation_by_sku("CSV-ROUTE-A") is not None)

    print("== route flow: invalid file blocks confirm ==")
    bad_data = {"mode": "full",
                "csv": (io.BytesIO(full_csv([
                    {"name": "Bad Route", "sku": "CSV-BADR",
                     "category": "bogus", "price": "10"},
                ]).encode()), "bad.csv")}
    r = c.post("/admin/products/import", data=bad_data,
               content_type="multipart/form-data")
    check("invalid upload shows errors", b"error" in r.data.lower())
    check("invalid preview has no confirm button",
          b"Confirm import" not in r.data)
    # Confirm re-validates server-side: stage a bad file under a forged
    # token and verify confirm refuses it (stale/tampered preview guard).
    forged = "f" * 32
    (appmod._import_staging_dir() / f"{forged}.csv").write_bytes(
        full_csv([{"name": "Bad Route", "sku": "CSV-BADR",
                   "category": "bogus", "price": "10"}]).encode())
    r = c.post("/admin/products/import/confirm",
               data={"token": forged, "mode": "full"})
    check("confirm of invalid file refused (400)", r.status_code == 400)
    check("refused confirm wrote nothing",
          db.get_product_by_sku("CSV-BADR") is None)

    print("== route guards ==")
    r = c.post("/admin/products/import/confirm",
               data={"token": "0" * 32, "mode": "full"},
               follow_redirects=False)
    check("bad token redirects to import page",
          r.status_code in (301, 302, 303)
          and "/admin/products/import" in r.headers.get("Location", ""))
    big = {"mode": "full",
           "csv": (io.BytesIO(b"x" * (csvimport.MAX_CSV_BYTES + 1)),
                   "big.csv")}
    r = c.post("/admin/products/import", data=big,
               content_type="multipart/form-data")
    check("over-5MB file rejected", r.status_code == 400
          and b"5 MB" in r.data)
    txt = {"mode": "full",
           "csv": (io.BytesIO(b"name,sku\nx,y"), "notes.txt")}
    r = c.post("/admin/products/import", data=txt,
               content_type="multipart/form-data")
    check("non-csv filename rejected", r.status_code == 400)

    print("== privacy: import internals stay admin-only ==")
    pub = c.get(f"/product/{pr['id']}")
    check("public product page loads", pub.status_code == 200)
    check("cost not leaked on public page", b"9.99" not in pub.data)
    check("msku not leaked on public page", b"M-ROUTE" not in pub.data)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:")
        for f in FAIL:
            print(" -", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
