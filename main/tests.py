from django.test import TestCase
from django.urls import reverse
from .models import Cart, CartProduct, Category, Product, User

class ProductDetailTestCase(TestCase):
    def setUp(self):
        # Create category
        self.category = Category.objects.create(
            name="Electronics",
            logo="test_logo.png",
            is_active=True
        )
        
        # Create products in the same category
        self.product1 = Product.objects.create(
            category=self.category,
            image="test_image1.png",
            name="Smartphone X",
            description="High-end smartphone",
            price=999.99,
            count=10
        )
        
        self.product2 = Product.objects.create(
            category=self.category,
            image="test_image2.png",
            name="Smartphone Y",
            description="Mid-range smartphone",
            price=499.99,
            count=20
        )
        
        self.product3 = Product.objects.create(
            category=self.category,
            image="test_image3.png",
            name="Tablet Z",
            description="Powerful tablet",
            price=299.99,
            count=5
        )

        # Create another category and a product in it to verify isolation
        self.other_category = Category.objects.create(
            name="Books",
            logo="test_logo2.png",
            is_active=True
        )
        
        self.other_product = Product.objects.create(
            category=self.other_category,
            image="test_book.png",
            name="Django for Beginners",
            description="Excellent book",
            price=39.99,
            count=50
        )

    def test_product_detail_related_products(self):
        url = reverse('product_detail', kwargs={'code': self.product1.code})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        
        # Assert related products are in context
        self.assertIn('related_products', response.context)
        related_products = response.context['related_products']
        
        # Assert product1 itself is not in the related products list
        self.assertNotIn(self.product1, related_products)
        
        # Assert product2 and product3 are in the related products list
        self.assertIn(self.product2, related_products)
        self.assertIn(self.product3, related_products)
        
        # Assert product from other category is not in the related products list
        self.assertNotIn(self.other_product, related_products)


class AuthAndCartFlowTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='buyer', password='secret123', phone='+998900001122', address='Toshkent')
        self.category = Category.objects.create(name='Electronics', logo='test_logo.png', is_active=True)
        self.product = Product.objects.create(
            category=self.category,
            image='test_image.png',
            name='Phone',
            description='Phone desc',
            price=100,
            discount_price=80,
            discount_status=True,
            count=5,
        )

    def test_register_short_username_shows_error(self):
        response = self.client.post(reverse('register'), {
            'username': 'abc',
            'phone': '901234567',
            'password': 'secret123',
            'confirm_password': 'secret123',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'kamida 4 ta belgidan iborat bo\'lishi kerak')

    def test_register_duplicate_username_shows_error(self):
        response = self.client.post(reverse('register'), {
            'username': self.user.username,
            'phone': '901234567',
            'password': 'secret123',
            'confirm_password': 'secret123',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'foydalanuvchi nomi allaqachon band')

    def test_register_duplicate_phone_shows_error(self):
        response = self.client.post(reverse('register'), {
            'username': 'newuser123',
            'phone': self.user.phone,
            'password': 'secret123',
            'confirm_password': 'secret123',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'telefon raqami allaqachon ro\'yxatdan o\'tgan')

    def test_login_invalid_credentials_shows_error(self):
        response = self.client.post(reverse('login'), {
            'username': self.user.username,
            'password': 'wrong-pass',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Foydalanuvchi nomi yoki parol noto')

    def test_remove_from_cart_only_affects_active_cart(self):
        active_cart = Cart.objects.create(user=self.user, status=1)
        delivered_cart = Cart.objects.create(user=self.user, status=4)
        active_item = CartProduct.objects.create(cart=active_cart, product=self.product, count=1)
        delivered_item = CartProduct.objects.create(cart=delivered_cart, product=self.product, count=2)

        self.client.force_login(self.user)
        response = self.client.post(reverse('remove_from_cart', args=[self.product.code]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(CartProduct.objects.filter(pk=active_item.pk).exists())
        self.assertTrue(CartProduct.objects.filter(pk=delivered_item.pk).exists())

    def test_checkout_moves_active_cart_to_order(self):
        active_cart = Cart.objects.create(user=self.user, status=1)
        CartProduct.objects.create(cart=active_cart, product=self.product, count=2)

        self.client.force_login(self.user)
        response = self.client.post(reverse('checkout'))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('order_detail', args=[active_cart.code]))
        active_cart.refresh_from_db()
        self.assertEqual(active_cart.status, 2)

    def test_order_history_shows_only_real_orders(self):
        Cart.objects.create(user=self.user, status=1)
        order = Cart.objects.create(user=self.user, status=2)
        CartProduct.objects.create(cart=order, product=self.product, count=1)

        self.client.force_login(self.user)
        response = self.client.get(reverse('profile'))

        self.assertEqual(response.status_code, 200)
        orders = list(response.context['orders'])
        self.assertEqual(orders, [order])

        redirect_response = self.client.get(reverse('order_history'))
        self.assertEqual(redirect_response.status_code, 302)

    def test_order_detail_is_limited_to_owner(self):
        other_user = User.objects.create_user(username='other', password='secret123')
        order = Cart.objects.create(user=other_user, status=2)

        self.client.force_login(self.user)
        response = self.client.get(reverse('order_detail', args=[order.code]))

        self.assertEqual(response.status_code, 404)

