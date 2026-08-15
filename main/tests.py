import json
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from .models import Cart, CartProduct, Category, Product, User, SiteSettings

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
        self.assertContains(response, 'kamida 4 ta belgidan iborat')

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
        self.assertContains(response, 'telefon raqami allaqachon')

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
        SiteSettings.objects.create(site_name="Chimyon-bozor", prepayment_enabled=False)
        active_cart = Cart.objects.create(user=self.user, status=1)
        CartProduct.objects.create(cart=active_cart, product=self.product, count=2)

        self.client.force_login(self.user)
        response = self.client.post(reverse('checkout'), {'provider': 'cash'})

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('payment_success', args=[active_cart.code]))
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

    @patch('main.sms_service.send_sms_code', return_value=True)
    def test_register_otp_flow_and_activation(self, mock_send_sms):
        # 1. Register new user
        response = self.client.post(reverse('register'), {
            'username': 'newbuyer',
            'phone': '917914881',
            'password': 'secretPassword123',
            'confirm_password': 'secretPassword123',
        })

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('verify_otp'))

        # Verify user created but inactive and phone_verified=False
        new_user = User.objects.get(username='newbuyer')
        self.assertFalse(new_user.is_active)
        self.assertFalse(new_user.phone_verified)

        # Verify OTP code created in database
        otp_obj = new_user.otp_codes.latest('created_at')
        self.assertIsNotNone(otp_obj)
        self.assertEqual(len(otp_obj.code), 6)

        # 2. Verify invalid OTP code fails
        fail_resp = self.client.post(reverse('verify_otp'), {
            'otp_code': '000000',
        })
        self.assertEqual(fail_resp.status_code, 200)
        self.assertContains(fail_resp, 'Kiritilgan SMS kod')

        # 3. Verify valid OTP code activates user and logs in
        success_resp = self.client.post(reverse('verify_otp'), {
            'otp_code': otp_obj.code,
        })
        self.assertEqual(success_resp.status_code, 302)
        self.assertRedirects(success_resp, reverse('index'))

        new_user.refresh_from_db()
        self.assertTrue(new_user.is_active)
        self.assertTrue(new_user.phone_verified)

    def test_shop_extras_formatting_filters(self):
        from main.templatetags.shop_extras import uz_price, intspace, compact_money, monthly_price

        # 1. uz_price
        self.assertEqual(uz_price(36068578000), "36 068 578 000 so'm")
        self.assertEqual(uz_price(1000200), "1 000 200 so'm")
        self.assertEqual(uz_price(0), "0 so'm")
        self.assertEqual(uz_price(None), "0 so'm")

        # 2. intspace
        self.assertEqual(intspace(57271), "57 271")
        self.assertEqual(intspace(36068578000), "36 068 578 000")
        self.assertEqual(intspace(0), "0")

        # 3. compact_money
        self.assertEqual(compact_money(36068578000), "36.07 mlrd so'm")
        self.assertEqual(compact_money(1200000), "1.2 mln so'm")
        self.assertEqual(compact_money(850000), "850 ming so'm")
        self.assertEqual(compact_money(500), "500 so'm")

        # 4. monthly_price
        self.assertEqual(monthly_price(1200000, 12), "100 000 so'm/oyiga")

    def test_update_cart_quantity_ajax_and_stock_cap(self):
        user = User.objects.create_user(username='cartuser', password='password123', is_active=True)
        self.client.force_login(user)

        category = Category.objects.create(name="Shirinliklar", is_active=True)
        product = Product.objects.create(
            category=category,
            name="Two Zero",
            price=300,
            count=10
        )

        cart = Cart.objects.create(user=user, status=1)
        cart_product = CartProduct.objects.create(cart=cart, product=product, count=1)

        # 1. Update quantity to 3
        url = reverse('update_cart_quantity', kwargs={'product_code': product.code})
        response = self.client.post(
            url,
            data=json.dumps({'quantity': 3}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'updated')
        self.assertEqual(data['count'], 3)
        self.assertEqual(data['item_total_price'], 900.0)
        self.assertIsNone(data['stock_warning'])

        # 2. Exceed stock (product has count=10, request 15)
        response = self.client.post(
            url,
            data=json.dumps({'quantity': 15}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'updated')
        self.assertEqual(data['count'], 10)
        self.assertIn('Omborda faqat 10 ta mahsulot mavjud', data['stock_warning'])

        # 3. Delete via quantity 0
        response = self.client.post(
            url,
            data=json.dumps({'quantity': 0}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'deleted')
        self.assertFalse(CartProduct.objects.filter(cart=cart, product=product).exists())

    def test_profile_username_and_phone_real_check(self):
        user1 = User.objects.create_user(username='testuser1', password='pass123', phone='+998901112233')
        user2 = User.objects.create_user(username='testuser2', password='pass123', phone='+998904445566')

        self.client.force_login(user1)

        # 1. Check own username (should be valid and current)
        res = self.client.get(reverse('check_username_api'), {'username': 'testuser1'})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['valid'])
        self.assertTrue(data.get('is_current'))

        # 2. Check other taken username (should be taken)
        res = self.client.get(reverse('check_username_api'), {'username': 'testuser2'})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data['valid'])
        self.assertFalse(data.get('available'))

        # 3. Check new free username (should be available)
        res = self.client.get(reverse('check_username_api'), {'username': 'brandnewuser'})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['valid'])
        self.assertTrue(data['available'])

        # 4. Check own phone (should be valid and current)
        res = self.client.get(reverse('check_phone_api'), {'phone': '+998901112233'})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['valid'])
        self.assertTrue(data.get('is_current'))

        # 5. Check other taken phone (should be taken)
        res = self.client.get(reverse('check_phone_api'), {'phone': '+998904445566'})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data['valid'])
        self.assertFalse(data.get('available'))

        # 6. Check new free phone (should be available)
        res = self.client.get(reverse('check_phone_api'), {'phone': '+998907778899'})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['valid'])
        self.assertTrue(data['available'])

        # 7. Update profile successfully
        res = self.client.post(reverse('profile'), {
            'first_name': 'Ali',
            'last_name': 'Valiyev',
            'username': 'brandnewuser',
            'phone': '+998907778899',
            'address': 'Chimyon'
        })
        self.assertEqual(res.status_code, 302)
        user1.refresh_from_db()
        self.assertEqual(user1.username, 'brandnewuser')
        self.assertEqual(user1.phone, '+998907778899')
        self.assertEqual(user1.first_name, 'Ali')


