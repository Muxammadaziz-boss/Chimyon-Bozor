import json
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from .models import Cart, CartProduct, Category, Product, User, SiteSettings
from .views import get_active_cart
from . import models

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
        self.assertTrue('10' in data['stock_warning'])

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

    def test_profile_image_crop_modal_rendered(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Check crop modal and UI elements
        self.assertIn('id="avatarCropModal"', content)
        self.assertIn('id="cropCanvas"', content)
        self.assertIn('id="cropZoomSlider"', content)
        self.assertIn('id="btnCropRotate"', content)
        self.assertIn('id="btnCropReset"', content)
        self.assertIn('id="btnCropConfirm"', content)
        self.assertIn('id="avatarNewPreviewCard"', content)
        self.assertIn('id="avatarCroppedPreviewImg"', content)
        self.assertIn('Profil rasmini sozlash', content)

    def test_profile_avatar_upload_valid_image(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile

        # Create a genuine 512x512 JPEG test image
        img = Image.new('RGB', (512, 512), color=(124, 58, 237))
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='JPEG')
        img_bytes.seek(0)

        uploaded_file = SimpleUploadedFile(
            name='avatar_test.jpg',
            content=img_bytes.read(),
            content_type='image/jpeg'
        )

        self.client.force_login(self.user)
        response = self.client.post(reverse('profile'), {
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'username': self.user.username,
            'phone': self.user.phone or '+998901234567',
            'address': 'Chimyon',
            'photo': uploaded_file
        })

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.photo)
        self.assertTrue(self.user.photo.name.endswith('.jpg'))

    def test_profile_avatar_upload_invalid_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        # Fake script file pretending to be an image
        fake_file = SimpleUploadedFile(
            name='malicious.php.jpg',
            content=b'<?php echo "evil"; ?>',
            content_type='image/jpeg'
        )

        self.client.force_login(self.user)
        response = self.client.post(reverse('profile'), {
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'username': self.user.username,
            'phone': self.user.phone or '+998901234567',
            'address': 'Chimyon',
            'photo': fake_file
        })

        # Should redirect back to profile with error message
        self.assertEqual(response.status_code, 302)

    def test_strict_phone_validation_valid_and_invalid(self):
        self.client.force_login(self.user)

        # 1. Valid numbers
        valid_numbers = ['+998901234567', '+998991234567']
        for num in valid_numbers:
            res = self.client.get(reverse('check_phone_api'), {'phone': num})
            data = res.json()
            self.assertTrue(data['valid'], f"Failed for valid number: {num}")

        # 2. Invalid numbers
        invalid_numbers = [
            '+998901234567a',
            'a+998901234567',
            '++998901234567',
            '+998+901234567',
            '+99890123456',
            '+9989012345678',
            '+997901234567',
            '+123456789'
        ]
        for num in invalid_numbers:
            res = self.client.get(reverse('check_phone_api'), {'phone': num})
            data = res.json()
            self.assertFalse(data['valid'], f"Expected invalid for number: {num}")

        # 3. Profile save with invalid phone with letter should fail
        res = self.client.post(reverse('profile'), {
            'first_name': 'Test',
            'last_name': 'User',
            'username': self.user.username,
            'phone': '+998917914881a',
            'address': 'Chimyon'
        })
        self.assertEqual(res.status_code, 302)
        self.assertNotEqual(self.user.phone, '+998917914881a')

    def test_canonical_phone_normalization(self):
        from main.validators import normalize_uz_phone

        # 1. Normalization function unit checks
        self.assertEqual(normalize_uz_phone('901234567'), '+998901234567')
        self.assertEqual(normalize_uz_phone('998901234567'), '+998901234567')
        self.assertEqual(normalize_uz_phone('+998901234567'), '+998901234567')
        self.assertEqual(normalize_uz_phone('+998 90 123 45 67'), '+998901234567')
        self.assertEqual(normalize_uz_phone('998 90 123 45 67'), '+998901234567')
        self.assertEqual(normalize_uz_phone('90 123 45 67'), '+998901234567')

        # 2. Invalid inputs
        self.assertIsNone(normalize_uz_phone('+998901234567a'))
        self.assertIsNone(normalize_uz_phone('+997901234567'))
        self.assertIsNone(normalize_uz_phone('+123456789'))
        self.assertIsNone(normalize_uz_phone(''))

        # 3. API canonical return
        self.client.force_login(self.user)
        res = self.client.get(reverse('check_phone_api'), {'phone': '917914881'})
        data = res.json()
        self.assertTrue(data['valid'])
        self.assertEqual(data.get('canonical'), '+998917914881')

        # 4. Profile save normalization into DB
        res = self.client.post(reverse('profile'), {
            'first_name': 'Canon',
            'last_name': 'Test',
            'username': self.user.username,
            'phone': '931234567',
            'address': 'Chimyon'
        })
        self.assertEqual(res.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '+998931234567')

    def test_realtime_validation_rapid_simulation(self):
        self.client.force_login(self.user)

        # 1. Simulate rapid username keystrokes: 'a' -> 'ab' -> 'abc' -> 'abcd' -> 'abcde'
        keystrokes = ['a', 'ab', 'abc', 'abcd', 'abcde']
        responses = []
        for term in keystrokes:
            res = self.client.get(reverse('check_username_api'), {'username': term})
            responses.append(res.json())

        # Sub-length (< 4) should be invalid
        self.assertFalse(responses[0]['available'])
        self.assertFalse(responses[1]['available'])
        self.assertFalse(responses[2]['available'])
        # Valid length (>= 4) should be valid and available
        self.assertTrue(responses[3]['available'])
        self.assertTrue(responses[4]['available'])

        # 2. Simulate rapid phone keystrokes: '+998' -> '+9989' -> '+99890' -> '+998901234567'
        phone_steps = ['+998', '+9989', '+99890', '+998901', '+998901234567']
        phone_responses = []
        for step in phone_steps:
            res = self.client.get(reverse('check_phone_api'), {'phone': step})
            phone_responses.append(res.json())

        self.assertFalse(phone_responses[0]['valid'])
        self.assertFalse(phone_responses[1]['valid'])
        self.assertFalse(phone_responses[2]['valid'])
        self.assertFalse(phone_responses[3]['valid'])
        self.assertTrue(phone_responses[4]['valid'])
        self.assertEqual(phone_responses[4]['canonical'], '+998901234567')


class ProductDetailUXTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='detailtester',
            password='password123',
            phone='+998901112233'
        )
        self.category = models.Category.objects.create(name='Texnika', is_active=True)
        self.in_stock_product = models.Product.objects.create(
            name='Noutbuk Pro 15',
            category=self.category,
            price=Decimal('10000000.00'),
            discount_price=Decimal('8500000.00'),
            discount_status=True,
            count=10,
            description='Zo\'r noutbuk tezkor va kuchli'
        )
        self.out_of_stock_product = models.Product.objects.create(
            name='Tugagan Smartfon',
            category=self.category,
            price=Decimal('3000000.00'),
            count=0,
            description='Omborda qolmagan smartfon'
        )

    def test_product_detail_in_stock_page_render(self):
        response = self.client.get(reverse('product_detail', kwargs={'code': self.in_stock_product.code}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Noutbuk Pro 15')
        self.assertContains(response, 'Sotuvda mavjud')
        self.assertContains(response, 'Savatga qo\'shish')
        self.assertContains(response, 'Hozir xarid qilish')
        self.assertEqual(self.in_stock_product.discount_percent, 15)

    def test_product_detail_out_of_stock_page_render(self):
        response = self.client.get(reverse('product_detail', kwargs={'code': self.out_of_stock_product.code}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tugagan Smartfon')
        self.assertContains(response, 'Sotuvda qolmagan')
        self.assertContains(response, 'disabled')

    def test_add_to_cart_stock_enforcement(self):
        self.client.login(username='detailtester', password='password123')
        
        # Adding more than available stock should cap at stock count
        response = self.client.post(
            reverse('add_to_cart', kwargs={'product_code': self.in_stock_product.code}),
            {'quantity': 99},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        
        cart = get_active_cart(self.user)
        cart_item = models.CartProduct.objects.get(cart=cart, product=self.in_stock_product)
        self.assertEqual(cart_item.count, 10)  # Capped at product.count (10)

    def test_out_of_stock_cart_rejection(self):
        self.client.login(username='detailtester', password='password123')
        response = self.client.post(
            reverse('add_to_cart', kwargs={'product_code': self.out_of_stock_product.code}),
            {'quantity': 1},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data['status'], 'error')

    def test_product_review_and_avg_rating(self):
        self.client.login(username='detailtester', password='password123')
        
        # Add review
        response = self.client.post(
            reverse('add_review', kwargs={'product_code': self.in_stock_product.code}),
            {'rating': 5, 'text': 'Ajoyib mahsulot, juda yoqdi!'}
        )
        self.assertEqual(response.status_code, 302)
        
        self.assertEqual(self.in_stock_product.reviews_count, 1)
        self.assertEqual(self.in_stock_product.avg_rating, 5.0)


class CartEdgeCaseConsistencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='carttester',
            password='password123',
            phone='+998909998877'
        )
        self.category = Category.objects.create(name='Aksessuarlar', is_active=True)
        self.product1 = Product.objects.create(
            name='Naushnik Air',
            category=self.category,
            price=Decimal('500000.00'),
            discount_price=Decimal('400000.00'),
            discount_status=True,
            count=10,
            description='Wireless headphones'
        )
        self.product2 = Product.objects.create(
            name='Sichqoncha RGB',
            category=self.category,
            price=Decimal('200000.00'),
            count=5,
            description='Gaming mouse'
        )
        self.cart = get_active_cart(self.user)
        self.item1 = CartProduct.objects.create(cart=self.cart, product=self.product1, count=10)
        self.item2 = CartProduct.objects.create(cart=self.cart, product=self.product2, count=3)

    # 1. Stock Decreased Edge Case: Cart had 10, stock dropped to 3 -> Cart page auto-caps and warns
    def test_stock_decreased_auto_cap(self):
        self.product1.count = 3
        self.product1.save()

        self.client.login(username='carttester', password='password123')
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.status_code, 200)

        self.item1.refresh_from_db()
        self.assertEqual(self.item1.count, 3)

    # 2. Out of Stock Edge Case: Stock = 0 -> In cart marked out-of-stock, Checkout blocked
    def test_out_of_stock_item_blocks_checkout(self):
        self.product1.count = 0
        self.product1.save()

        self.client.login(username='carttester', password='password123')
        response = self.client.get(reverse('checkout'))
        # Should redirect back to cart with error
        self.assertEqual(response.status_code, 302)
        self.assertIn('/cart/', response.url)

    # 3. Quantity Cap via AJAX update
    def test_quantity_cap_via_ajax_update(self):
        self.client.login(username='carttester', password='password123')
        # product2 only has 5 in stock, requesting 20
        response = self.client.post(
            reverse('update_cart_quantity', kwargs={'product_code': self.product2.code}),
            data=json.dumps({'quantity': 20}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'updated')
        self.assertEqual(data['count'], 5)
        self.assertEqual(data['stock_warning'], 'Faqat 5 dona mavjud.')

    # 4. Price Change: Authoritative DB price is used for calculation
    def test_price_change_authoritative(self):
        # Change product price on server
        self.product2.price = Decimal('350000.00')
        self.product2.save()

        self.client.login(username='carttester', password='password123')
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.status_code, 200)
        
        # total for item2 = 3 * 350000 = 1,050,000
        self.item2.refresh_from_db()
        self.assertEqual(self.item2.total_price, Decimal('1050000.00'))

    # 5. Discount Change: If discount removed, total price uses original price
    def test_discount_change_reflected(self):
        self.product1.discount_status = False
        self.product1.save()

        self.item1.refresh_from_db()
        # count 10 * 500000 = 5,000,000 (was 400000)
        self.assertEqual(self.item1.total_price, Decimal('5000000.00'))

    # 6. Negative or Invalid Quantity Rejection
    def test_invalid_quantity_rejection(self):
        self.client.login(username='carttester', password='password123')
        response = self.client.post(
            reverse('update_cart_quantity', kwargs={'product_code': self.product2.code}),
            data=json.dumps({'quantity': -5}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data['status'], 'error')

    # 7. Delete Item via AJAX
    def test_delete_cart_item(self):
        self.client.login(username='carttester', password='password123')
        response = self.client.post(
            reverse('remove_from_cart', kwargs={'product_code': self.product2.code}),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertFalse(CartProduct.objects.filter(cart=self.cart, product=self.product2).exists())

    # 8. Multi-tab Stale Cart at Checkout POST
    def test_multi_tab_stale_stock_at_checkout_post(self):
        self.client.login(username='carttester', password='password123')
        # Another tab decreases stock to 1
        self.product1.count = 1
        self.product1.save()

        response = self.client.post(
            reverse('checkout'),
            {
                'phone': '+998909998877',
                'address': 'Chimyonskiy bozor',
                'provider': 'cash',
                'payment_method': 'cash'
            }
        )
        # Should redirect back to cart and adjust quantity
        self.assertEqual(response.status_code, 302)
        self.assertIn('/cart/', response.url)
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.count, 1)









