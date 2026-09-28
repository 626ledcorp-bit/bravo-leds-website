#!/usr/bin/env python3
'''Interior-kit opt-in for the fitment page bundle builder.

- The "Add interior light kit" opt-in appears only when the vehicle has a
  matching interior kit (2020 Toyota 4Runner does; 2010 Toyota Prius does not).
- Default unchecked.
- Checked kit lands in the cart as its own line item at the server-side
  price, and the kit total includes it.
- A kit item for a different vehicle (or no vehicle) is rejected server-side.
- Unchecked/absent kit changes nothing.

Run:  cd website && .venv/bin/python test_fit_interior_kit.py
'''
import os
import re
import sys
import tempfile

WEB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WEB)

os.environ['STORE_DB'] = os.path.join(tempfile.mkdtemp(prefix='fitkit_'),
                                      'store.db')
os.environ['FITMENT_DIR'] = os.path.join(os.path.dirname(WEB), 'fitment')
for _v in ('STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY',
           'STRIPE_WEBHOOK_SECRET', 'ADMIN_PASSWORD', 'SMTP_HOST',
           'SMTP_PORT', 'SMTP_USER', 'SMTP_PASS', 'SMTP_FROM',
           'ORDER_NOTIFY_EMAIL'):
    os.environ.pop(_v, None)

import app as appmod  # noqa: E402
import kits  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print('  [' + ('ok' if cond else 'FAIL') + '] ' + name
          + (' -- ' + extra if extra and not cond else ''))


def client():
    return appmod.app.test_client()


KIT_PID = 'kit-2020-toyota-4runner'
KIT_VID = kits.KIT_VARIATION_ID
KIT_PRICE = kits.KIT_PRICE_CENTS


def optin_block(html):
    m = re.search(r'<div class="ik-optin" id="ik-optin"(.*?)>', html, re.S)
    return m.group(1) if m else None


def first_series_row(html):
    m = re.search(r'<div class="series-row[^"]*"[^>]*data-pid="([^"]+)" '
                  r'data-vid="([^"]+)"[^>]*data-price="(\d+)"', html)
    return (m.group(1), m.group(2), int(m.group(3))) if m else (None, None, 0)


def kit_card(html):
    m = re.search(r'<div class="kit-card">(.*?)</button>', html, re.S)
    return m.group(0) if m else None


def test_optin_present_when_kit_exists():
    html = client().get('/fit/2020/toyota/4runner/interior'
                        ).get_data(as_text=True)
    card = kit_card(html)
    check('kit card present for kit vehicle', card is not None)
    check('kit card carries kit product id',
          card is not None and 'data-pid="%s"' % KIT_PID in card,
          (card or '')[:200])
    check('kit card carries kit variation id',
          card is not None and 'data-vid="%s"' % KIT_VID in card)
    check('kit card carries kit price',
          card is not None and 'Add — $%.2f' % (KIT_PRICE / 100) in card)
    check('kit card has add button',
          'id="kitcard-add"' in html)
    check('no headlight in page copy', 'headlight' not in html.lower())


def test_optin_absent_without_kit():
    html = client().get('/fit/2010/toyota/4runner').get_data(as_text=True)
    check('no opt-in block for non-kit vehicle', 'id="ik-optin"' not in html)
    check('no dead kit UI for non-kit vehicle', 'id="ik-check"' not in html)
    intr = client().get('/fit/2010/toyota/4runner/interior'
                        ).get_data(as_text=True)
    check('no kit card on interior page for non-kit vehicle',
          'id="kitcard-add"' not in intr)


def test_kit_adds_as_own_line():
    c = client()
    r = c.post('/api/cart/add-kit', json={
        'items': [{'product_id': KIT_PID, 'variation_id': KIT_VID,
                   'qty': 1}],
        'vehicle': {'year': 2020, 'make': 'Toyota', 'model': '4Runner'},
    })
    j = r.get_json()
    check('kit add 200 + ok', r.status_code == 200 and j.get('ok'),
          'status=%d j=%s' % (r.status_code, j))
    check('kit added 1 line', j.get('added') == 1, 'j=%s' % (j,))
    check('kit total is server price',
          j.get('total_cents') == KIT_PRICE, 'j=%s' % (j,))
    with c.session_transaction() as s:
        cart = s.get('cart', {})
    key = '%s::v%s' % (KIT_PID, KIT_VID)
    check('cart has the kit line', key in cart, 'keys=%s' % (list(cart),))


def test_kit_rejected_for_wrong_vehicle():
    c = client()
    r = c.post('/api/cart/add-kit', json={
        'items': [{'product_id': KIT_PID, 'variation_id': KIT_VID,
                   'qty': 1}],
        'vehicle': {'year': 2010, 'make': 'Toyota', 'model': 'Prius'},
    })
    check('wrong-vehicle kit -> 400', r.status_code == 400,
          'status=%d' % r.status_code)
    with c.session_transaction() as s:
        check('wrong-vehicle kit not in cart', not s.get('cart'))
    r = c.post('/api/cart/add-kit', json={
        'items': [{'product_id': KIT_PID, 'variation_id': KIT_VID,
                   'qty': 1}],
    })
    check('kit with no vehicle -> 400', r.status_code == 400,
          'status=%d' % r.status_code)


def test_kit_combines_with_positions():
    c = client()
    html = c.get('/fit/2020/toyota/4runner/forward').get_data(as_text=True)
    pid, vid, price = first_series_row(html)
    check('4runner page has series rows', bool(pid))
    if not pid:
        return
    r = c.post('/api/cart/add-kit', json={
        'items': [{'product_id': pid, 'variation_id': vid, 'qty': 1},
                  {'product_id': KIT_PID, 'variation_id': KIT_VID,
                   'qty': 1}],
        'vehicle': {'year': 2020, 'make': 'Toyota', 'model': '4Runner'},
    })
    j = r.get_json()
    check('combo add 200 + ok', r.status_code == 200 and j.get('ok'),
          'j=%s' % (j,))
    check('combo added 2 lines', j.get('added') == 2, 'j=%s' % (j,))
    check('combo total includes kit',
          j.get('total_cents') == price + KIT_PRICE, 'j=%s' % (j,))


def test_unchecked_kit_changes_nothing():
    c = client()
    html = c.get('/fit/2020/toyota/4runner/forward').get_data(as_text=True)
    pid, vid, price = first_series_row(html)
    r = c.post('/api/cart/add-kit', json={
        'items': [{'product_id': pid, 'variation_id': vid, 'qty': 1}],
        'vehicle': {'year': 2020, 'make': 'Toyota', 'model': '4Runner'},
    })
    j = r.get_json()
    check('no-kit combo 200 + ok', r.status_code == 200 and j.get('ok'))
    check('no-kit combo added 1 line', j.get('added') == 1, 'j=%s' % (j,))
    check('no-kit combo total is just the position',
          j.get('total_cents') == price, 'j=%s' % (j,))


if __name__ == '__main__':
    for fn in (test_optin_present_when_kit_exists,
               test_optin_absent_without_kit,
               test_kit_adds_as_own_line,
               test_kit_rejected_for_wrong_vehicle,
               test_kit_combines_with_positions,
               test_unchecked_kit_changes_nothing):
        print(fn.__name__)
        fn()
    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)
