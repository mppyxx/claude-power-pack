"""Reference fix used only to validate the hidden checker (never shown to the agent)."""
from decimal import Decimal, ROUND_HALF_UP

TAX_RATE = Decimal("0.08")
FREE_SHIPPING_MIN = Decimal("50.00")
SHIPPING_FEE = Decimal("5.99")
CENT = Decimal("0.01")


def _d(x):
    return Decimal(str(x))


def _round(x):
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def subtotal(items):
    return float(_round(sum((_d(i["price"]) * i["qty"] for i in items), Decimal(0))))


def order_total(items, coupon=None):
    sub = _round(sum((_d(i["price"]) * i["qty"] for i in items), Decimal(0)))
    if not coupon:
        disc = Decimal(0)
    elif coupon["type"] == "percent":
        disc = _round(sub * _d(coupon["value"]) / 100)
    elif coupon["type"] == "fixed":
        disc = _round(_d(coupon["value"]))
    else:
        raise ValueError(coupon["type"])
    disc = min(disc, sub)
    merch = sub - disc
    tax = _round(merch * TAX_RATE)
    ship = Decimal(0) if merch >= FREE_SHIPPING_MIN else SHIPPING_FEE
    total = merch + tax + ship
    return {k: float(v) for k, v in dict(subtotal=sub, discount=disc, tax=tax, shipping=ship, total=total).items()}
