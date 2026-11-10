"""Hidden checks for the bugfix task. Not shown to the agent.

Usage: python3 -I hidden_tests.py <dir containing checkout.py>
Prints one JSON object: {"score": n, "max": 10, "tests": {...}}

Each test encodes the pricing rules written in the checkout.py docstring.
Planted bugs in the starting code:
  A (reported)  tax is computed on the pre-coupon subtotal
  B             a coupon larger than the subtotal makes the merchandise negative
  C             nothing is rounded to cents (and halves must round up)
  D             free shipping is decided on the pre-coupon subtotal
"""
import json
import sys
import traceback

TOL = 0.0005


def eq(a, b):
    return abs(float(a) - b) < TOL


def cents(x):
    v = float(x) * 100
    c = round(v)
    if abs(v - c) > 1e-6:
        raise AssertionError(f"{x!r} is not a whole number of cents")
    return int(c)


def item(price, qty, sku="SKU"):
    return {"sku": sku, "price": price, "qty": qty}


def run(dirpath):
    sys.path.insert(0, dirpath)
    try:
        import checkout  # noqa
    except Exception:
        return {"score": 0, "max": 10, "import_error": traceback.format_exc(limit=3), "tests": {}}

    ot = checkout.order_total

    def t01_regression_no_coupon():
        r = ot([item(30.00, 2)])
        assert eq(r["subtotal"], 60.00) and eq(r["tax"], 4.80) and eq(r["shipping"], 0) and eq(r["total"], 64.80), r

    def t02_regression_shipping_under_threshold():
        r = ot([item(20.00, 1)])
        assert eq(r["tax"], 1.60) and eq(r["shipping"], 5.99) and eq(r["total"], 27.59), r

    def t03_reported_case_percent_coupon():
        r = ot([item(50.00, 3)], {"type": "percent", "value": 20})
        assert eq(r["discount"], 30.00) and eq(r["tax"], 9.60) and eq(r["total"], 129.60), r

    def t04_fixed_coupon_tax_after_discount():
        r = ot([item(115.00, 1)], {"type": "fixed", "value": 15.00})
        assert eq(r["tax"], 8.00) and eq(r["shipping"], 0) and eq(r["total"], 108.00), r

    def t05_coupon_larger_than_order_total():
        r = ot([item(8.00, 1)], {"type": "fixed", "value": 10.00})
        assert eq(r["total"], 5.99), r

    def t06_coupon_larger_than_order_receipt_lines():
        r = ot([item(8.00, 1)], {"type": "fixed", "value": 10.00})
        assert eq(r["discount"], 8.00) and eq(r["tax"], 0.00) and eq(r["shipping"], 5.99), r

    def t07_free_shipping_uses_amount_after_coupon():
        r = ot([item(55.00, 1)], {"type": "percent", "value": 10})
        assert eq(r["discount"], 5.50) and eq(r["shipping"], 5.99) and eq(r["tax"], 3.96) and eq(r["total"], 59.45), r

    def t08_rounded_to_cents():
        r = ot([item(19.99, 3)])
        assert eq(r["subtotal"], 59.97) and eq(r["tax"], 4.80) and eq(r["total"], 64.77), r

    def t09_receipt_adds_up():
        carts = [
            ([item(19.99, 3)], None),
            ([item(12.10, 1)], {"type": "percent", "value": 15}),
            ([item(33.33, 1)], {"type": "percent", "value": 10}),
            ([item(7.77, 3), item(1.11, 2)], {"type": "percent", "value": 12.5}),
        ]
        for items, coupon in carts:
            r = ot(items, coupon)
            s, d, t, sh, tot = (cents(r[k]) for k in ("subtotal", "discount", "tax", "shipping", "total"))
            assert s - d + t + sh == tot, (items, coupon, r)

    def t10_half_cent_rounds_up():
        r = ot([item(12.10, 1)], {"type": "percent", "value": 15})
        assert eq(r["discount"], 1.82) and eq(r["tax"], 0.82) and eq(r["total"], 17.09), r

    tests = [t01_regression_no_coupon, t02_regression_shipping_under_threshold, t03_reported_case_percent_coupon,
             t04_fixed_coupon_tax_after_discount, t05_coupon_larger_than_order_total,
             t06_coupon_larger_than_order_receipt_lines, t07_free_shipping_uses_amount_after_coupon,
             t08_rounded_to_cents, t09_receipt_adds_up, t10_half_cent_rounds_up]
    out = {}
    for t in tests:
        try:
            t()
            out[t.__name__] = {"pass": True}
        except Exception as e:  # noqa
            out[t.__name__] = {"pass": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    return {"score": sum(1 for v in out.values() if v["pass"]), "max": len(tests), "tests": out}


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1])))
