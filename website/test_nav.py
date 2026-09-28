"""Admin-editable navigation tests: seeded tree, DB-driven rendering,
admin add/edit/toggle/move/delete, changes effective immediately."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("STORE_DB", "/tmp/test_nav.db")
if os.path.exists("/tmp/test_nav.db"):
    os.remove("/tmp/test_nav.db")

import db
import app as A

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name, detail)


A.app.config["TESTING"] = True
c = A.app.test_client()

tree = db.get_nav_tree()
labels = [t["label"] for t in tree]
check("seeded top-level order",
      labels == ["By Vehicle", "By Bulb Size", "LED Bulbs", "Shop All",
                 "Track Order", "Contact Us"], str(labels))

by_size = [t for t in tree if t["label"] == "By Bulb Size"][0]
check("16 bulb sizes seeded",
      len(by_size["children"]) == 16 and by_size["has_sizelinks"])
led = [t for t in tree if t["label"] == "LED Bulbs"][0]
kinds = {x["kind"] for x in led["children"]}
check("LED panel has tiles + footer links",
      kinds == {"tile", "morelink"} and led["has_tiles"], str(kinds))

r = c.get("/")
h = r.get_data(as_text=True)
check("home 200", r.status_code == 200)
nav = h.split('<nav class="nav"')[1].split("</nav>")[0]
for s in ["By Vehicle", "By Bulb Size", "LED Bulbs", "Track Order",
          "Contact Us", "nav-panel-mega", "size-grid", "ph-bulb.svg",
          "/search?q=H11", "Complete Interior Kits"]:
    check("header contains " + s, s in nav)

# every menu link returns 200
import re
bad = []
for u in sorted(set(re.findall(r'href="(/[^"#]*)"', nav))):
    rr = c.get(u)
    if rr.status_code != 200:
        bad.append((u, rr.status_code))
check("all header menu urls 200", not bad, str(bad))

# admin page requires login
check("nav admin redirects when logged out",
      c.get("/admin/navigation").status_code == 302)
with c.session_transaction() as s:
    s["admin_authed"] = True
r = c.get("/admin/navigation")
check("nav admin 200 when logged in", r.status_code == 200)
check("nav admin shows tree",
      "By Vehicle" in r.get_data(as_text=True))

# hide takes effect immediately
to = [i for i in db.list_nav_items() if i["label"] == "Track Order"][0]
db.update_nav_item(to["id"], to["label"], to["link"], to["kind"],
                   to["image"], 0, visible=0)
nav = c.get("/").get_data(as_text=True).split('<nav class="nav"')[1].split("</nav>")[0]
check("hide removes item from header", "Track Order" not in nav)
db.update_nav_item(to["id"], to["label"], to["link"], to["kind"],
                   to["image"], 0, visible=1)

# reorder takes effect immediately
cu = [i for i in db.list_nav_items() if i["label"] == "Contact Us"][0]
check("move up returns True", db.move_nav_item(cu["id"], "up"))
nav = c.get("/").get_data(as_text=True).split('<nav class="nav"')[1].split("</nav>")[0]
check("reorder reflected in header",
      nav.index("Contact Us") < nav.index("Track Order"))
db.move_nav_item(cu["id"], "down")

# add + delete round trip
nid = db.add_nav_item(None, "Clearance", "/shop", "link")
check("add appears in header",
      "Clearance" in c.get("/").get_data(as_text=True))
db.delete_nav_item(nid)
check("delete removes from header",
      "Clearance" not in c.get("/").get_data(as_text=True)
      .split('<nav class="nav"')[1].split("</nav>")[0])

# fitment session write happens only after fitment confirmed
c2 = A.app.test_client()
r = c2.get("/fitment?year=2099&make=Nope&model=Nothing")
check("bad fitment 404", r.status_code == 404)
with c2.session_transaction() as s:
    check("bad fitment does not set vehicle pill",
          "vehicle" not in s)

print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)


def main():
    pass
