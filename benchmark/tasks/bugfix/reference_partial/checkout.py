"""Checkout pricing for Lumen Tea Co.

Pricing rules (agreed with finance):

1. Subtotal is the sum of price * qty over the cart lines.
2. A coupon is optional and applies to the subtotal, before tax.
   - percent coupon: {"type": "percent", "value": 20} means 20% off the subtotal.
   - fixed coupon:   {"type": "fixed", "value": 10.00} means $10.00 off.
   A coupon can bring the merchandise cost down to $0.00 but never below it.
3. Sales tax is 8% of the merchandise cost after the coupon.
4. Shipping is free when the merchandise cost after the coupon is $50.00 or
   more. Otherwise shipping is a flat $5.99. Shipping is not taxed.
5. Every money amount (subtotal, discount, tax, shipping, total) is rounded to
   whole cents, with halves rounding up (0.005 -> 0.01), and the receipt has to
   add up: subtotal - discount + tax + shipping == total.

order_total() returns the receipt as a dict of floats in dollars.
"""

TAX_RATE = 0.08
FREE_SHIPPING_MIN = 50.00
SHIPPING_FEE = 5.99


def subtotal(items):
    """items: list of {"sku": str, "price": float, "qty": int}"""
    return sum(item["price"] * item["qty"] for item in items)


def coupon_discount(amount, coupon):
    if not coupon:
        return 0.0
    if coupon["type"] == "percent":
        return amount * coupon["value"] / 100
    if coupon["type"] == "fixed":
        return coupon["value"]
    raise ValueError(f"unknown coupon type: {coupon['type']}")


def shipping_cost(amount):
    if amount >= FREE_SHIPPING_MIN:
        return 0.0
    return SHIPPING_FEE


def order_total(items, coupon=None):
    sub = subtotal(items)
    discount = coupon_discount(sub, coupon)
    tax = (sub - discount) * TAX_RATE
    shipping = shipping_cost(sub)
    total = sub - discount + tax + shipping
    return {
        "subtotal": sub,
        "discount": discount,
        "tax": tax,
        "shipping": shipping,
        "total": total,
    }
