"""2FA (TOTP) login tests for the admin.

Covers: password step -> code step, wrong codes rejected, backup codes
(one-time), brute-force throttling, no-secret fallback, and the settings
backup-code regeneration.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pyotp

TMP = tempfile.mkdtemp()
os.environ["STORE_DB"] = os.path.join(TMP, "store.db")
os.environ["FITMENT_DIR"] = os.path.join(TMP, "fitment")
os.environ["SEMA_DIR"] = os.path.join(TMP, "sema")
os.environ["ADMIN_PASSWORD"] = "test-admin-pw"
SECRET = "JBSWY3DPEHPK3PXP"  # fixed test secret ("Hello!" in base32)
os.environ["ADMIN_TOTP_SECRET"] = SECRET

import db  # noqa: E402
import app as appmod  # noqa: E402

db.init_db()
appmod.app.config["TESTING"] = True

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name)


def fresh_client():
    appmod._LOGIN_ATTEMPTS.clear()
    c = appmod.app.test_client()
    # unique IP per client so throttling state doesn't leak between tests
    c.environ_base["REMOTE_ADDR"] = f"10.9.{fresh_client.n}.1"
    fresh_client.n += 1
    return c


fresh_client.n = 0


def login_password(c, pw="test-admin-pw"):
    return c.post("/admin/login", data={"password": pw},
                  follow_redirects=False)


def do_full_login(c):
    """Password step + valid TOTP step. Returns final response."""
    r = login_password(c)
    assert r.status_code == 302 and "/admin/login/2fa" in r.headers["Location"], \
        f"password step: {r.status_code}"
    code = pyotp.TOTP(SECRET).now()
    return c.post("/admin/login/2fa", data={"code": code},
                  follow_redirects=False)


# 1. Password-only step redirects to the 2FA page, not straight in.
c = fresh_client()
r = login_password(c)
check("pw ok -> redirect to 2fa", r.status_code == 302
      and r.headers["Location"].endswith("/admin/login/2fa"))
r = c.get("/admin", follow_redirects=False)
check("not authed before code", r.status_code == 302
      and "/admin/login" in r.headers["Location"])

# 2. 2FA page without the password step bounces back to login.
c2 = fresh_client()
r = c2.get("/admin/login/2fa", follow_redirects=False)
check("2fa page needs pw step first", r.status_code == 302
      and r.headers["Location"].endswith("/admin/login"))

# 3. Wrong password still 401.
c = fresh_client()
r = login_password(c, "nope")
check("wrong password -> 401", r.status_code == 401)

# 4. Wrong code rejected, right code accepted.
c = fresh_client()
login_password(c)
r = c.post("/admin/login/2fa", data={"code": "000000"})
check("wrong code rejected", r.status_code == 200
      and b"Wrong code" in r.data)
r = c.get("/admin", follow_redirects=False)
check("still not authed after wrong code", r.status_code == 302)
r = do_full_login(fresh_client())
check("right code -> redirect to dashboard", r.status_code == 302
      and r.headers["Location"].endswith("/admin"))

# 5. Full login actually authenticates.
c = fresh_client()
do_full_login(c)
r = c.get("/admin")
check("authed after 2fa", r.status_code == 200)

# 6. Backup codes: work once, then burn.
c = fresh_client()
login_password(c)
codes = appmod._generate_backup_codes(4)
db.set_totp_backup_hashes([appmod._hash_backup_code(x) for x in codes])
r = c.post("/admin/login/2fa", data={"code": codes[0]},
           follow_redirects=False)
check("backup code accepted", r.status_code == 302
      and r.headers["Location"].endswith("/admin"))
check("backup code burned", codes[0] not in
      [x for x in db.get_totp_backup_hashes()])
c = fresh_client()
login_password(c)
r = c.post("/admin/login/2fa", data={"code": codes[0]})
check("used backup code rejected", b"Wrong code" in r.data)
r = c.post("/admin/login/2fa", data={"code": codes[1]},
           follow_redirects=False)
check("second backup code accepted", r.status_code == 302)

# 7. Backup code format is unambiguous and unique.
batch = appmod._generate_backup_codes(50)
check("codes unique", len(set(batch)) == 50)
check("codes well-formed",
      all(len(x) == 9 and x[4] == "-" for x in batch))

# 8. Brute-force throttle on the password form.
c = fresh_client()
for _ in range(6):
    c.post("/admin/login", data={"password": "wrong"})
r = c.post("/admin/login", data={"password": "wrong"})
check("password throttled after 6 fails", r.status_code == 429)

# 9. Brute-force throttle on the code form.
c = fresh_client()
login_password(c)
for _ in range(6):
    c.post("/admin/login/2fa", data={"code": "000000"})
r = c.post("/admin/login/2fa", data={"code": "000000"})
check("code throttled after 6 fails", r.status_code == 429)

# 10. No TOTP secret -> password-only login still works (fallback).
del os.environ["ADMIN_TOTP_SECRET"]
c = fresh_client()
r = login_password(c, "test-admin-pw")
check("no secret -> straight to dashboard", r.status_code == 302
      and r.headers["Location"].endswith("/admin"))
os.environ["ADMIN_TOTP_SECRET"] = SECRET

# 11. Settings page shows 2FA status + regenerates backup codes.
c = fresh_client()
do_full_login(c)
r = c.get("/admin/settings")
check("settings shows 2fa enabled", b"2FA is" in r.data and b"enabled" in r.data)
r = c.post("/admin/settings/2fa-codes")
check("regenerate shows new codes once",
      r.status_code == 200 and b"won't be shown again" in r.data)
check("10 codes stored hashed", len(db.get_totp_backup_hashes()) == 10)

# 12. Old backup codes die when regenerated.
c = fresh_client()
login_password(c)
old = appmod._generate_backup_codes(2)
db.set_totp_backup_hashes([appmod._hash_backup_code(x) for x in old])
c2 = fresh_client()
do_full_login(c2)
c2.post("/admin/settings/2fa-codes")  # regenerate
c = fresh_client()
login_password(c)
r = c.post("/admin/login/2fa", data={"code": old[0]})
check("old backup code dead after regen", b"Wrong code" in r.data)

# 13. Open-redirect guard on ?next=.
c = fresh_client()
r = c.post("/admin/login?next=https://evil.example.com",
           data={"password": "test-admin-pw"}, follow_redirects=False)
check("absolute next= rejected",
      r.headers["Location"].endswith("/admin/login/2fa"))
del os.environ["ADMIN_TOTP_SECRET"]
c = fresh_client()
r = c.post("/admin/login?next=//evil.example.com",
           data={"password": "test-admin-pw"}, follow_redirects=False)
check("protocol-relative next= rejected",
      r.headers["Location"].endswith("/admin"))
os.environ["ADMIN_TOTP_SECRET"] = SECRET

print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
