from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from main.models import Cart, CartProduct, Category, Product, User


class DashboardOrderLogicTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='admin', password='secret123')
        self.customer = User.objects.create_user(username='customer', password='secret123')
        self.category = Category.objects.create(name='Category', logo='test_logo.png', is_active=True)
        self.product = Product.objects.create(
            category=self.category,
            image='test_image.png',
            name='Product',
            description='Desc',
            price=100,
            discount_price=80,
            discount_status=True,
            count=10,
        )

    def test_dashboard_index_counts_only_real_orders(self):
        Cart.objects.create(user=self.customer, status=1)
        Cart.objects.create(user=self.customer, status=2)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('d_index'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stats']['pending_orders'], 1)
        self.assertEqual(response.context['stats']['total_orders'], 1)

    def test_export_orders_uses_discounted_total_and_excludes_active_carts(self):
        Cart.objects.create(user=self.customer, status=1)
        delivered = Cart.objects.create(user=self.customer, status=4)
        CartProduct.objects.create(cart=delivered, product=self.product, count=2)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('d_export_orders'))

        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        sheet = workbook.active
        self.assertEqual(sheet.max_row, 2)
        self.assertEqual(sheet['F2'].value, 160)
        self.assertEqual(sheet['G2'].value, 40)
        self.assertEqual(sheet['H2'].value, 160)
