import unittest

from checkout import order_total, subtotal


class CheckoutTest(unittest.TestCase):
    def test_subtotal(self):
        items = [{"sku": "GRN-01", "price": 12.50, "qty": 2}, {"sku": "OOL-02", "price": 5.00, "qty": 1}]
        self.assertAlmostEqual(subtotal(items), 30.00, places=2)

    def test_free_shipping_over_threshold(self):
        r = order_total([{"sku": "GRN-01", "price": 30.00, "qty": 2}])
        self.assertAlmostEqual(r["shipping"], 0.00, places=2)
        self.assertAlmostEqual(r["total"], 64.80, places=2)

    def test_shipping_under_threshold(self):
        r = order_total([{"sku": "BLK-03", "price": 20.00, "qty": 1}])
        self.assertAlmostEqual(r["shipping"], 5.99, places=2)
        self.assertAlmostEqual(r["total"], 27.59, places=2)

    def test_percent_coupon_discount(self):
        r = order_total([{"sku": "GRN-01", "price": 40.00, "qty": 1}], {"type": "percent", "value": 10})
        self.assertAlmostEqual(r["discount"], 4.00, places=2)


if __name__ == "__main__":
    unittest.main()
