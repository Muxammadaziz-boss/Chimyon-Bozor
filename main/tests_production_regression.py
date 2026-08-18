from django.test import TestCase

from main import models


class ReleasedInventoryRetryTests(TestCase):
    def test_released_order_re_reserves_stock_before_payment_retry(self):
        user = models.User.objects.create_user(
            username='inventory_retry_user',
            password='TestPassword123!',
            phone='+998901112233',
            phone_verified=True,
        )
        category = models.Category.objects.create(name='Inventory retry category')
        product = models.Product.objects.create(
            name='Inventory retry product',
            category=category,
            description='Regression test product',
            price=10000,
            count=2,
        )
        order = models.Cart.objects.create(user=user, status=2)
        models.CartProduct.objects.create(cart=order, product=product, count=1)

        self.assertTrue(order.reserve_inventory())
        product.refresh_from_db()
        self.assertEqual(product.count, 1)

        self.assertTrue(order.release_inventory())
        product.refresh_from_db()
        self.assertEqual(product.count, 2)

        # A payment retry must reserve the returned unit again, not leave it sellable.
        self.assertTrue(order.reserve_inventory())
        product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(product.count, 1)
        self.assertEqual(order.inventory_status, models.Cart.InventoryStatus.RESERVED)
