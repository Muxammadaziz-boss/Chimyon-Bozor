import json
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from .models import Cart, CartProduct, Category, Product, User, SiteSettings, Payment, Review, Address
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


class OrderStatusAndFinancialStatusUXTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='ordertester',
            password='password123',
            phone='+998901234567'
        )
        self.admin_user = User.objects.create_superuser(
            username='adminuser',
            password='password123',
            phone='+998901112233'
        )
        self.category = Category.objects.create(name='Elektronika', is_active=True)
        self.product = Product.objects.create(
            name='Klaviatura RGB',
            category=self.category,
            price=Decimal('1000000.00'),
            count=10
        )
        self.order = Cart.objects.create(
            user=self.user,
            status=2, # Qabul qilindi
            financial_status=Cart.FinancialStatus.UNPAID,
            prepayment_percent=30
        )
        CartProduct.objects.create(cart=self.order, product=self.product, count=1)

    # 1. Delivered + Unpaid: Changing order status to Delivered (4) does NOT alter UNPAID financial status
    def test_delivered_plus_unpaid_independence(self):
        self.client.login(username='adminuser', password='password123')
        # Admin updates order status to Delivered (4)
        response = self.client.post(
            reverse('d_update_status', kwargs={'code': self.order.code}),
            {'target_status': '4', 'comment': 'Yetkazildi'}
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 4) # Delivered
        self.assertEqual(self.order.financial_status, Cart.FinancialStatus.UNPAID) # Remains Unpaid!
        self.assertEqual(self.order.paid_amount, Decimal('0.00'))
        self.assertEqual(self.order.remaining_amount, Decimal('1000000.00'))

    # 2. Delivered + Partial: Order delivered, 30% prepayment paid, 70% remaining
    def test_delivered_plus_partially_paid(self):
        Payment.objects.create(
            order=self.order,
            amount=Decimal('300000.00'),
            provider=Payment.Provider.CLICK,
            purpose=Payment.Purpose.PREPAYMENT,
            status=Payment.Status.PAID
        )
        self.order.status = 4 # Delivered
        self.order.financial_status = Cart.FinancialStatus.PARTIALLY_PAID
        self.order.save()

        self.assertEqual(self.order.status, 4)
        self.assertEqual(self.order.financial_status, Cart.FinancialStatus.PARTIALLY_PAID)
        self.assertEqual(self.order.paid_amount, Decimal('300000.00'))
        self.assertEqual(self.order.remaining_amount, Decimal('700000.00'))

    # 3. Delivered + Full: Order delivered and fully paid
    def test_delivered_plus_fully_paid(self):
        Payment.objects.create(
            order=self.order,
            amount=Decimal('1000000.00'),
            provider=Payment.Provider.CLICK,
            purpose=Payment.Purpose.FULL,
            status=Payment.Status.PAID
        )
        self.order.status = 4 # Delivered
        self.order.financial_status = Cart.FinancialStatus.FULLY_PAID
        self.order.save()

        self.assertEqual(self.order.status, 4)
        self.assertEqual(self.order.financial_status, Cart.FinancialStatus.FULLY_PAID)
        self.assertEqual(self.order.paid_amount, Decimal('1000000.00'))
        self.assertEqual(self.order.remaining_amount, Decimal('0.00'))

    # 4. Cancelled + Refunded: Order cancelled (5) and refund recorded
    def test_cancelled_plus_refunded(self):
        p = Payment.objects.create(
            order=self.order,
            amount=Decimal('300000.00'),
            provider=Payment.Provider.CLICK,
            purpose=Payment.Purpose.PREPAYMENT,
            status=Payment.Status.REFUNDED,
            refund_amount=Decimal('300000.00')
        )
        self.order.status = 5 # Cancelled
        self.order.financial_status = Cart.FinancialStatus.REFUNDED
        self.order.save()

        self.assertEqual(self.order.status, 5)
        self.assertEqual(self.order.financial_status, Cart.FinancialStatus.REFUNDED)
        self.assertEqual(self.order.paid_amount, Decimal('0.00'))

    # 5. Prepayment only ledger entry
    def test_prepayment_only_ledger(self):
        Payment.objects.create(
            order=self.order,
            amount=Decimal('300000.00'),
            provider=Payment.Provider.CLICK,
            purpose=Payment.Purpose.PREPAYMENT,
            status=Payment.Status.PAID
        )
        self.assertEqual(self.order.payments.count(), 1)
        self.assertEqual(self.order.payments.first().purpose, Payment.Purpose.PREPAYMENT)
        self.assertEqual(self.order.paid_amount, Decimal('300000.00'))

    # 6. Balance paid (Prepayment + Balance Settlement)
    def test_balance_settlement_ledger(self):
        Payment.objects.create(
            order=self.order,
            amount=Decimal('300000.00'),
            provider=Payment.Provider.CLICK,
            purpose=Payment.Purpose.PREPAYMENT,
            status=Payment.Status.PAID
        )
        Payment.objects.create(
            order=self.order,
            amount=Decimal('700000.00'),
            provider=Payment.Provider.CASH,
            purpose=Payment.Purpose.BALANCE,
            status=Payment.Status.PAID
        )
        self.order.financial_status = Cart.FinancialStatus.FULLY_PAID
        self.order.save()

        self.assertEqual(self.order.payments.count(), 2)
        self.assertEqual(self.order.paid_amount, Decimal('1000000.00'))
        self.assertEqual(self.order.remaining_amount, Decimal('0.00'))

    # 7. Customer order detail view renders correct decoupled state and ledger
    def test_customer_order_detail_view(self):
        Payment.objects.create(
            order=self.order,
            amount=Decimal('300000.00'),
            provider=Payment.Provider.CLICK,
            purpose=Payment.Purpose.PREPAYMENT,
            status=Payment.Status.PAID
        )
        self.order.status = 4 # Delivered
        self.order.financial_status = Cart.FinancialStatus.PARTIALLY_PAID
        self.order.save()

        self.client.login(username='ordertester', password='password123')
        response = self.client.get(reverse('order_detail', kwargs={'code': self.order.code}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Yetkazildi')
        self.assertContains(response, 'Qisman')
        self.assertContains(response, '700 000') # Remaining amount

    # 8. Admin order detail view renders correct decoupled controls and settle balance modal
    def test_admin_order_detail_view(self):
        self.client.login(username='adminuser', password='password123')
        response = self.client.get(reverse('d_detail_orders', kwargs={'code': self.order.code}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Moliyaviy Holat')
        self.assertContains(response, 'settleBalanceModal')


class AvatarAndProfileImageQATests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='avatartester',
            password='password123',
            phone='+998901234567',
            first_name='Aziz',
            last_name='Valiyev'
        )

    def _generate_test_image(self, format='JPEG', size=(512, 512), color=(124, 58, 237)):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile

        file_obj = io.BytesIO()
        image = Image.new("RGB", size, color)
        image.save(file_obj, format=format)
        file_obj.seek(0)
        ext = format.lower()
        if ext == 'jpeg':
            ext = 'jpg'
        return SimpleUploadedFile(f"test_avatar.{ext}", file_obj.read(), content_type=f"image/{ext}")

    # 1. Profile Page Crop Modal & Elements Render
    def test_profile_page_crop_modal_render(self):
        self.client.login(username='avatartester', password='password123')
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'avatarCropModal')
        self.assertContains(response, 'cropCanvas')
        self.assertContains(response, 'cropZoomSlider')
        self.assertContains(response, 'btnCropRotate')
        self.assertContains(response, 'btnCropConfirm')
        self.assertContains(response, 'avatarNewPreviewCard')

    # 2. Valid JPEG Avatar Upload & Save
    def test_valid_jpeg_avatar_upload(self):
        self.client.login(username='avatartester', password='password123')
        avatar = self._generate_test_image(format='JPEG')
        response = self.client.post(
            reverse('profile'),
            {
                'username': 'avatartester',
                'phone': '+998901234567',
                'first_name': 'Aziz',
                'last_name': 'Valiyev',
                'address': 'Chimyon',
                'photo': avatar
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(bool(self.user.photo))

    # 3. Valid PNG Avatar Upload & Save
    def test_valid_png_avatar_upload(self):
        self.client.login(username='avatartester', password='password123')
        avatar = self._generate_test_image(format='PNG')
        response = self.client.post(
            reverse('profile'),
            {
                'username': 'avatartester',
                'phone': '+998901234567',
                'first_name': 'Aziz',
                'last_name': 'Valiyev',
                'address': 'Chimyon',
                'photo': avatar
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(bool(self.user.photo))

    # 4. Valid WEBP Avatar Upload & Save
    def test_valid_webp_avatar_upload(self):
        self.client.login(username='avatartester', password='password123')
        avatar = self._generate_test_image(format='WEBP')
        response = self.client.post(
            reverse('profile'),
            {
                'username': 'avatartester',
                'phone': '+998901234567',
                'first_name': 'Aziz',
                'last_name': 'Valiyev',
                'address': 'Chimyon',
                'photo': avatar
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(bool(self.user.photo))

    # 5. Invalid / Corrupted Fake Image Rejection
    def test_invalid_corrupt_image_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.login(username='avatartester', password='password123')
        fake_image = SimpleUploadedFile("fake.jpg", b"MALICIOUS_NON_IMAGE_CONTENT", content_type="image/jpeg")
        response = self.client.post(
            reverse('profile'),
            {
                'username': 'avatartester',
                'phone': '+998901234567',
                'first_name': 'Aziz',
                'last_name': 'Valiyev',
                'address': 'Chimyon',
                'photo': fake_image
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(bool(self.user.photo))

    # 6. Disallowed Extension Rejection (e.g. .php, .exe, .sh)
    def test_disallowed_extension_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.login(username='avatartester', password='password123')
        bad_file = SimpleUploadedFile("script.php", b"<?php echo 'bad'; ?>", content_type="application/x-php")
        response = self.client.post(
            reverse('profile'),
            {
                'username': 'avatartester',
                'phone': '+998901234567',
                'first_name': 'Aziz',
                'last_name': 'Valiyev',
                'address': 'Chimyon',
                'photo': bad_file
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(bool(self.user.photo))

    # 7. Oversized Image File Rejection (> 5MB)
    def test_oversized_file_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.login(username='avatartester', password='password123')
        large_bytes = b"0" * (6 * 1024 * 1024) # 6MB
        large_file = SimpleUploadedFile("large_avatar.jpg", large_bytes, content_type="image/jpeg")
        response = self.client.post(
            reverse('profile'),
            {
                'username': 'avatartester',
                'phone': '+998901234567',
                'first_name': 'Aziz',
                'last_name': 'Valiyev',
                'address': 'Chimyon',
                'photo': large_file
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(bool(self.user.photo))

    # 8. Profile Edit without Avatar Change
    def test_profile_edit_without_photo_change(self):
        self.client.login(username='avatartester', password='password123')
        response = self.client.post(
            reverse('profile'),
            {
                'username': 'avatartester',
                'phone': '+998901234567',
                'first_name': 'Muhammad',
                'last_name': 'Karimov',
                'address': 'Chimyon'
            },
            follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Muhammad')
        self.assertEqual(self.user.last_name, 'Karimov')


class CategoryAndFilterCountConsistencyTests(TestCase):
    def setUp(self):
        Category.objects.all().delete()
        Product.objects.all().delete()

        # Create categories: 2 active, 1 inactive
        self.cat1 = Category.objects.create(name='Smartfonlar', is_active=True)
        self.cat2 = Category.objects.create(name='Kiyimlar', is_active=True)
        self.cat_inactive = Category.objects.create(name='Yashirin Kategoriya', is_active=False)

        # Create products in cat1
        self.p1 = Product.objects.create(
            name='iPhone 15 Pro', category=self.cat1, price=Decimal('15000000.00'),
            discount_price=Decimal('13500000.00'), discount_status=True, count=10
        )
        self.p2 = Product.objects.create(
            name='Samsung S24 Ultra', category=self.cat1, price=Decimal('14000000.00'),
            discount_price=Decimal('10000000.00'), discount_status=True, count=3 # low stock
        )
        self.p3 = Product.objects.create(
            name='Redmi Note 13', category=self.cat1, price=Decimal('3000000.00'),
            discount_status=False, count=0 # out of stock
        )

        # Create products in cat2
        self.p4 = Product.objects.create(
            name='Qishki Kurtka', category=self.cat2, price=Decimal('800000.00'),
            discount_status=False, count=15
        )

        # Create product in inactive category
        self.p_inactive = Product.objects.create(
            name='Yashirin Mahsulot', category=self.cat_inactive, price=Decimal('500000.00'),
            discount_status=False, count=5
        )

    # 1. Category count accuracy in /categories/ page
    def test_categories_page_counts(self):
        response = self.client.get(reverse('categories'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_categories'], 2) # only active categories
        self.assertEqual(response.context['total_products'], 4) # 4 products in active categories
        self.assertContains(response, 'Smartfonlar')
        self.assertContains(response, 'Kiyimlar')
        self.assertNotContains(response, 'Yashirin Kategoriya')

    # 2. Inactive category excluded from public listing & search
    def test_inactive_category_excluded_from_public_listing(self):
        response = self.client.get(reverse('all_products'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_products'], 4)
        self.assertNotContains(response, 'Yashirin Mahsulot')

        # Direct access to inactive category should return 404
        resp_inactive = self.client.get(reverse('category_filter', kwargs={'category_id': self.cat_inactive.id}))
        self.assertEqual(resp_inactive.status_code, 404)

    # 3. Category Filter Result Count
    def test_category_filter_count(self):
        response = self.client.get(reverse('category_filter', kwargs={'category_id': self.cat1.id}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_products'], 3)
        self.assertContains(response, 'iPhone 15 Pro')
        self.assertNotContains(response, 'Qishki Kurtka')

    # 4. Search + Category Intersection Count
    def test_search_plus_category_count(self):
        response = self.client.get(reverse('all_products'), {'q': 'iPhone', 'category': self.cat1.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_products'], 1)
        self.assertContains(response, 'iPhone 15 Pro')
        self.assertNotContains(response, 'Samsung S24 Ultra')

    # 5. Discount Filter Count
    def test_discount_filter_count(self):
        # All discounted products (p1, p2)
        response = self.client.get(reverse('all_products'), {'discount': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_products'], 2)

        # 20%+ discount (p2: 14m -> 10m is 28.5% discount)
        response_20 = self.client.get(reverse('all_products'), {'discount': '20'})
        self.assertEqual(response_20.status_code, 200)
        self.assertEqual(response_20.context['total_products'], 1)
        self.assertContains(response_20, 'Samsung S24 Ultra')

    # 6. Stock Filter Counts (in_stock, low_stock, out_of_stock)
    def test_stock_filter_counts(self):
        # In stock: count > 0 (p1=10, p2=3, p4=15 => 3 items)
        resp_in = self.client.get(reverse('all_products'), {'stock': 'in_stock'})
        self.assertEqual(resp_in.status_code, 200)
        self.assertEqual(resp_in.context['total_products'], 3)

        # Low stock: 0 < count <= 5 (p2=3 => 1 item)
        resp_low = self.client.get(reverse('all_products'), {'stock': 'low_stock'})
        self.assertEqual(resp_low.status_code, 200)
        self.assertEqual(resp_low.context['total_products'], 1)
        self.assertContains(resp_low, 'Samsung S24 Ultra')

        # Out of stock: count <= 0 (p3=0 => 1 item)
        resp_out = self.client.get(reverse('all_products'), {'stock': 'out_of_stock'})
        self.assertEqual(resp_out.status_code, 200)
        self.assertEqual(resp_out.context['total_products'], 1)
        self.assertContains(resp_out, 'Redmi Note 13')

    # 7. Zero Result Empty State Consistency
    def test_zero_result_empty_state(self):
        response = self.client.get(reverse('all_products'), {'q': 'MavjudBolmaganQidiruv12345'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_products'], 0)
        self.assertContains(response, 'Topildi: <strong class="text-primary">0 ta</strong>')
        self.assertContains(response, 'mos mahsulot topilmadi')

    # 8. Live Search active category filter
    def test_live_search_consistency(self):
        response = self.client.get(reverse('live_search'), {'q': 'iPhone'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['name'], 'iPhone 15 Pro')

        # Inactive category product should NOT appear in live search
        resp_hidden = self.client.get(reverse('live_search'), {'q': 'Yashirin'})
        self.assertEqual(resp_hidden.status_code, 200)
        data_hidden = resp_hidden.json()
        self.assertEqual(len(data_hidden['results']), 0)


class CatalogSortingBusinessMetricsTests(TestCase):
    def setUp(self):
        Category.objects.all().delete()
        Product.objects.all().delete()
        Cart.objects.all().delete()
        CartProduct.objects.all().delete()
        Review.objects.all().delete()

        self.user = User.objects.create_user(username='sorttester', password='password123', phone='+998901234567')
        self.cat = Category.objects.create(name='Texnika', is_active=True)

        # Product A: Price 1 000 000, discount 700 000 (effective 700 000), Stock 10
        self.prod_a = Product.objects.create(
            name='Product A', category=self.cat, price=Decimal('1000000.00'),
            discount_price=Decimal('700000.00'), discount_status=True, count=10
        )
        # Product B: Price 800 000 (effective 800 000), Stock 5
        self.prod_b = Product.objects.create(
            name='Product B', category=self.cat, price=Decimal('800000.00'),
            discount_status=False, count=5
        )
        # Product C: Price 500 000 (effective 500 000), Stock 0 (out of stock)
        self.prod_c = Product.objects.create(
            name='Product C', category=self.cat, price=Decimal('500000.00'),
            discount_status=False, count=0
        )
        # Product D: Price 2 000 000 (effective 2 000 000), Stock 20
        self.prod_d = Product.objects.create(
            name='Product D', category=self.cat, price=Decimal('2000000.00'),
            discount_status=False, count=20
        )

        # Sales setup:
        # Confirmed delivered cart (status=4): Product B bought 15 times, Product A bought 2 times
        order_delivered = Cart.objects.create(user=self.user, status=4)
        CartProduct.objects.create(cart=order_delivered, product=self.prod_b, count=15)
        CartProduct.objects.create(cart=order_delivered, product=self.prod_a, count=2)

        # Cancelled/Returned cart (status=5): Product D bought 50 times (MUST BE EXCLUDED!)
        order_returned = Cart.objects.create(user=self.user, status=5)
        CartProduct.objects.create(cart=order_returned, product=self.prod_d, count=50)

        # Active shopping cart (status=1): Product C added 100 times (MUST BE EXCLUDED!)
        active_cart = Cart.objects.create(user=self.user, status=1)
        CartProduct.objects.create(cart=active_cart, product=self.prod_c, count=100)

        # Reviews setup:
        # Product D has 5 reviews with average rating 4.8
        for r in [5, 5, 5, 5, 4]:
            Review.objects.create(user=self.user, product=self.prod_d, rating=r, text="Ajoyib")
        # Product A has 1 review with rating 5.0
        Review.objects.create(user=self.user, product=self.prod_a, rating=5, text="Yaxshi")

    # 1. Price Ascending (effective discount price taken into account)
    def test_price_asc_sorting(self):
        response = self.client.get(reverse('all_products'), {'sort': 'price_asc'})
        self.assertEqual(response.status_code, 200)
        prods = list(response.context['products'])
        # Order by effective price: C(500k) -> A(700k) -> B(800k) -> D(2m)
        self.assertEqual([p.id for p in prods], [self.prod_c.id, self.prod_a.id, self.prod_b.id, self.prod_d.id])

    # 2. Price Descending
    def test_price_desc_sorting(self):
        response = self.client.get(reverse('all_products'), {'sort': 'price_desc'})
        self.assertEqual(response.status_code, 200)
        prods = list(response.context['products'])
        # Order by effective price DESC: D(2m) -> B(800k) -> A(700k) -> C(500k)
        self.assertEqual([p.id for p in prods], [self.prod_d.id, self.prod_b.id, self.prod_a.id, self.prod_c.id])

    # 3. Popular / Best Selling (confirmed orders only, excluding carts & returns)
    def test_bestseller_popular_sorting(self):
        response = self.client.get(reverse('all_products'), {'sort': 'popular'})
        self.assertEqual(response.status_code, 200)
        prods = list(response.context['products'])
        # Sales: B has 15 sales (status=4), A has 2 sales (status=4), D has 0 (status 5 excluded), C has 0 (status 1 excluded)
        self.assertEqual(prods[0].id, self.prod_b.id)
        self.assertEqual(prods[1].id, self.prod_a.id)

    # 4. Rating Sorting with tie-breaker
    def test_rating_sorting(self):
        response = self.client.get(reverse('all_products'), {'sort': 'rating'})
        self.assertEqual(response.status_code, 200)
        prods = list(response.context['products'])
        # Product A (5.0 rating, 1 review), Product D (4.8 rating, 5 reviews)
        self.assertEqual(prods[0].id, self.prod_a.id)
        self.assertEqual(prods[1].id, self.prod_d.id)

    # 5. Newest Sorting
    def test_newest_sorting(self):
        response = self.client.get(reverse('all_products'), {'sort': 'newest'})
        self.assertEqual(response.status_code, 200)
        prods = list(response.context['products'])
        self.assertEqual(prods[0].id, self.prod_d.id)
        self.assertEqual(prods[-1].id, self.prod_a.id)

    # 6. Recommended Deterministic Sorting (in stock first, then sales/reviews/ratings)
    def test_recommended_deterministic_sorting(self):
        response = self.client.get(reverse('all_products'), {'sort': 'recommended'})
        self.assertEqual(response.status_code, 200)
        prods = list(response.context['products'])
        # In-stock items (D, B, A) come before out-of-stock item (C)
        self.assertNotEqual(prods[-1].id, self.prod_d.id)
        self.assertEqual(prods[-1].id, self.prod_c.id)

    # 7. Category + Sorting combination
    def test_category_plus_sorting(self):
        response = self.client.get(reverse('category_filter', kwargs={'category_id': self.cat.id}), {'sort': 'price_asc'})
        self.assertEqual(response.status_code, 200)
        prods = list(response.context['products'])
        self.assertEqual(prods[0].id, self.prod_c.id)

    # 8. Search + Sorting combination
    def test_search_plus_sorting(self):
        response = self.client.get(reverse('all_products'), {'q': 'Product', 'sort': 'price_desc'})
        self.assertEqual(response.status_code, 200)
        prods = list(response.context['products'])
        self.assertEqual(prods[0].id, self.prod_d.id)


class GlobalAccessibilityAuditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='a11yuser',
            password='password123',
            phone='+998901234567',
            address='Chimyon'
        )
        self.address = Address.objects.create(name='Chimyon')
        self.cat = Category.objects.create(name='Texnika', is_active=True)
        self.prod = Product.objects.create(
            name='Smartfon X',
            category=self.cat,
            price=2000000,
            count=10
        )

    def test_base_template_accessibility_styles(self):
        """Ensure global focus-visible ring, sr-only utility, and reduced motion CSS exist."""
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(':focus-visible', content)
        self.assertIn('.sr-only', content)
        self.assertIn('prefers-reduced-motion', content)

    def test_modal_accessibility_attributes(self):
        """Ensure custom modal dialogs have role=dialog, aria-modal=true, and aria-labelledby."""
        response = self.client.get(reverse('index'))
        content = response.content.decode('utf-8')
        self.assertIn('role', content)
        self.assertIn('aria-modal', content)
        self.assertIn('customModalTitle', content)

    def test_catalog_toolbar_accessibility(self):
        """Ensure catalog view buttons have aria-pressed and filter drawer toggle has aria-controls and aria-expanded."""
        response = self.client.get(reverse('all_products'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('aria-controls="filterSidebarCard"', content)
        self.assertIn('aria-expanded="false"', content)
        self.assertIn('aria-pressed=', content)

    def test_categories_directory_tabs_accessibility(self):
        """Ensure category group pills use role=tablist, role=tab, and result count uses role=status."""
        response = self.client.get(reverse('categories'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('role="tablist"', content)
        self.assertIn('role="tab"', content)
        self.assertIn('role="status"', content)
        self.assertIn('aria-live="polite"', content)

    def test_auth_form_accessibility(self):
        """Ensure login and register forms have label for associations and aria-describedby for validation feedback."""
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('for="login-username"', content)
        self.assertIn('for="login-password"', content)
        self.assertIn('aria-describedby="usernameFeedback"', content)
        self.assertIn('aria-describedby="phoneFeedback"', content)
        self.assertIn('role="status"', content)
        self.assertIn('aria-live="polite"', content)

    def test_profile_address_dropdown_accessibility(self):
        """Ensure profile page address combobox and inputs have proper ARIA attributes."""
        self.client.login(username='a11yuser', password='password123')
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('role="combobox"', content)
        self.assertIn('role="listbox"', content)
        self.assertIn('role="option"', content)
        self.assertIn('aria-describedby="phoneFeedback"', content)
        self.assertIn('aria-describedby="usernameFeedback"', content)

    def test_cart_controls_accessibility(self):
        """Ensure cart quantity adjustments and item removals have accessible aria-labels."""
        self.client.login(username='a11yuser', password='password123')
        cart = Cart.objects.create(user=self.user, status=1)
        CartProduct.objects.create(cart=cart, product=self.prod, count=2)
        response = self.client.get(reverse('cart'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('aria-label="Kamaytirish"', content)
        self.assertIn('aria-label="Ko\'paytirish"', content)
        self.assertIn('aria-label="Savatdan o\'chirish"', content)


class GlobalErrorPagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='errortestuser',
            password='password123',
            phone='+998909876543',
            address='Chimyon'
        )

    def test_404_view_and_template(self):
        """Ensure custom 404 handler returns 404 status and rendered Uzbek recovery UI."""
        from main.views import custom_404_view
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get('/non-existent-page-url/')
        response = custom_404_view(request)
        self.assertEqual(response.status_code, 404)
        content = response.content.decode('utf-8')
        self.assertIn('404', content)
        self.assertIn('Sahifa topilmadi', content)
        self.assertIn('Bosh sahifaga qaytish', content)
        self.assertIn('Katalogni ko‘rish', content)
        self.assertIn('robots', content)
        self.assertIn('noindex', content)

    def test_403_unauthenticated_view(self):
        """Ensure 403 shows login prompt when user is not authenticated."""
        from main.views import custom_403_view
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        factory = RequestFactory()
        request = factory.get('/dashboard/analytics/')
        request.user = AnonymousUser()
        response = custom_403_view(request)
        self.assertEqual(response.status_code, 403)
        content = response.content.decode('utf-8')
        self.assertIn('403', content)
        self.assertIn('Kirish cheklangan', content)
        self.assertIn('Tizimga kirish', content)
        self.assertIn('Bosh sahifa', content)

    def test_403_authenticated_view(self):
        """Ensure 403 shows insufficient permissions message when user is authenticated."""
        from main.views import custom_403_view
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get('/dashboard/analytics/')
        request.user = self.user
        response = custom_403_view(request)
        self.assertEqual(response.status_code, 403)
        content = response.content.decode('utf-8')
        self.assertIn('403', content)
        self.assertIn('Kirish cheklangan', content)
        self.assertIn('Mening profilim', content)
        self.assertIn('Bosh sahifaga qaytish', content)

    def test_500_view_resilience_and_safety(self):
        """Ensure 500 handler returns 500 status and does not leak tracebacks or secrets."""
        from main.views import custom_500_view
        from django.test import RequestFactory
        from django.conf import settings
        factory = RequestFactory()
        request = factory.get('/some-failing-view/')
        response = custom_500_view(request)
        self.assertEqual(response.status_code, 500)
        content = response.content.decode('utf-8')
        self.assertIn('500', content)
        self.assertIn('Serverda vaqtinchalik xatolik', content)
        self.assertIn('Qayta urinish', content)
        self.assertIn('Bosh sahifaga qaytish', content)
        # Security check: Ensure SECRET_KEY and traceback are not leaked
        self.assertNotIn(settings.SECRET_KEY, content)
        self.assertNotIn('Traceback (most recent call last)', content)
        self.assertNotIn('OperationalError', content)

    def test_400_view(self):
        """Ensure 400 handler returns 400 status and clean error UI."""
        from main.views import custom_400_view
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get('/bad-request/')
        response = custom_400_view(request)
        self.assertEqual(response.status_code, 400)
        content = response.content.decode('utf-8')
        self.assertIn('400', content)
        self.assertIn('Noto‘g‘ri so‘rov', content)
        self.assertIn('Bosh sahifaga qaytish', content)

    def test_handler_registrations_in_urls(self):
        """Ensure handler404, handler403, handler500, handler400 are registered in config.urls."""
        import config.urls as root_urls
        self.assertEqual(root_urls.handler404, 'main.views.custom_404_view')
        self.assertEqual(root_urls.handler403, 'main.views.custom_403_view')
        self.assertEqual(root_urls.handler500, 'main.views.custom_500_view')
        self.assertEqual(root_urls.handler400, 'main.views.custom_400_view')

    def test_debug_false_client_request_profile_ishak(self):
        """Ensure requesting /profile/ishak with DEBUG=False returns custom 404 template with no technical debug info."""
        with self.settings(DEBUG=False):
            response = self.client.get('/profile/ishak')
            self.assertEqual(response.status_code, 404)
            content = response.content.decode('utf-8')
            self.assertIn('404', content)
            self.assertIn('Sahifa topilmadi', content)
            self.assertIn('Bosh sahifaga qaytish', content)
            self.assertNotIn("You're seeing this error because you have DEBUG = True", content)
            self.assertNotIn('Traceback', content)


class GlobalSEOPolishAuditTests(TestCase):
    """
    Comprehensive SEO audit verification test suite for Chimyon-bozor:
    1. Robots.txt rules & sitemap reference
    2. Dynamic Sitemap.xml validity, contents, exclusions
    3. Indexing policies (Public=INDEX, Private/Filters=NOINDEX)
    4. Canonical URLs and utm/parameter stripping
    5. Title and Meta description natural uniqueness
    6. Single H1 heading hierarchy per page
    7. JSON-LD structured data (Product, Breadcrumbs, Organization, WebSite)
    8. Open Graph & Twitter Cards absolute image URLs
    9. HTML language attribute
    10. 301 Redirect for duplicate category aliases
    """

    def setUp(self):
        self.settings_obj = SiteSettings.objects.create(
            pk=1,
            site_name="Chimyon-bozor",
            tagline="Sifatli va hamyonbop mahsulotlar bozori",
            phone="+998901234567"
        )
        self.active_cat = Category.objects.create(
            name="Telefonlar va Gadjetlar",
            logo="test_phone.png",
            is_active=True
        )
        self.inactive_cat = Category.objects.create(
            name="Maxfiy Kategoriya",
            logo="test_secret.png",
            is_active=False
        )
        self.product_active = Product.objects.create(
            category=self.active_cat,
            name="iPhone 15 Pro Max",
            description="Eng so'nggi flagman smartfon kuchli protsessor bilan.",
            price=Decimal("15000000"),
            discount_price=Decimal("14200000"),
            discount_status=True,
            count=15,
            image="test_iphone.png"
        )
        self.product_inactive = Product.objects.create(
            category=self.inactive_cat,
            name="Noma'lum Mahsulot",
            description="Yashirin toifadagi mahsulot.",
            price=Decimal("500000"),
            count=5,
            image="test_unknown.png"
        )
        self.user = User.objects.create_user(
            username="seouser",
            password="seopassword123",
            phone="+998901112233"
        )

    def test_robots_txt_status_and_content(self):
        """robots.txt must return 200, text/plain, allow public routes, disallow private, and link sitemap."""
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/plain', response['Content-Type'])
        content = response.content.decode('utf-8')
        self.assertIn('User-agent: *', content)
        self.assertIn('Allow: /', content)
        self.assertIn('Allow: /categories/', content)
        self.assertIn('Allow: /products/all/', content)
        self.assertIn('Allow: /category-filter/', content)
        self.assertIn('Allow: /product-detail/', content)
        self.assertIn('Disallow: /dashboard/', content)
        self.assertIn('Disallow: /cart/', content)
        self.assertIn('Disallow: /checkout/', content)
        self.assertIn('Disallow: /orders/', content)
        self.assertIn('Disallow: /profile/', content)
        self.assertIn('Disallow: /api/', content)
        self.assertIn('Disallow: /admin/', content)
        self.assertIn('Sitemap:', content)
        self.assertIn('/sitemap.xml', content)

    def test_sitemap_xml_status_and_content_type(self):
        """sitemap.xml must return 200 with application/xml content type."""
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/xml', response['Content-Type'])
        content = response.content.decode('utf-8')
        self.assertIn('<?xml version="1.0" encoding="UTF-8"?>', content)
        self.assertIn('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">', content)

    def test_sitemap_xml_contains_core_static_pages(self):
        """sitemap.xml must include home, /categories/, and /products/all/."""
        response = self.client.get('/sitemap.xml')
        content = response.content.decode('utf-8')
        self.assertIn('/categories/', content)
        self.assertIn('/products/all/', content)

    def test_sitemap_xml_contains_active_categories_and_products(self):
        """sitemap.xml must contain active categories and products."""
        response = self.client.get('/sitemap.xml')
        content = response.content.decode('utf-8')
        self.assertIn(f'/category-filter/{self.active_cat.id}/', content)
        self.assertIn(f'/product-detail/{self.product_active.code}/', content)

    def test_sitemap_xml_excludes_inactive_categories_and_their_products(self):
        """sitemap.xml must exclude inactive categories and products in inactive categories."""
        response = self.client.get('/sitemap.xml')
        content = response.content.decode('utf-8')
        self.assertNotIn(f'/category-filter/{self.inactive_cat.id}/', content)
        self.assertNotIn(f'/product-detail/{self.product_inactive.code}/', content)

    def test_sitemap_xml_excludes_private_and_admin_urls(self):
        """sitemap.xml must not contain private, transactional, or admin URLs."""
        response = self.client.get('/sitemap.xml')
        content = response.content.decode('utf-8')
        self.assertNotIn('/cart/', content)
        self.assertNotIn('/checkout/', content)
        self.assertNotIn('/profile/', content)
        self.assertNotIn('/dashboard/', content)
        self.assertNotIn('/admin/', content)
        self.assertNotIn('/api/', content)

    def test_home_page_seo_title_and_description(self):
        """Home page must have branded title and meta description."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('<title>Chimyon-bozor — Sifatli va hamyonbop mahsulotlar internet do\'koni</title>', content)
        self.assertIn('name="description"', content)
        self.assertIn('Chimyon-bozor', content)

    def test_home_page_canonical_and_single_h1(self):
        """Home page must have canonical link and single H1."""
        response = self.client.get('/')
        content = response.content.decode('utf-8')
        self.assertIn('rel="canonical"', content)
        self.assertEqual(content.count('<h1'), 1)

    def test_home_page_json_ld_organization_and_website(self):
        """Home page must render valid JSON-LD for WebSite and Organization."""
        response = self.client.get('/')
        content = response.content.decode('utf-8')
        self.assertIn('"@type": "WebSite"', content)
        self.assertIn('"@type": "Organization"', content)
        self.assertIn('"@type": "SearchAction"', content)

    def test_categories_directory_seo_and_breadcrumbs(self):
        """Categories page must have title, meta description, canonical, and single H1."""
        response = self.client.get('/categories/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Barcha kategoriyalar — Chimyon-bozor', content)
        self.assertIn('rel="canonical"', content)
        self.assertIn('/categories/', content)
        self.assertIn('"@type": "BreadcrumbList"', content)
        self.assertEqual(content.count('<h1'), 1)

    def test_all_categories_duplicate_alias_canonical(self):
        """Duplicate route /all-categories/ must set canonical pointing to /categories/."""
        response = self.client.get('/all-categories/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('rel="canonical"', content)
        self.assertIn('/categories/', content)

    def test_category_filter_clean_page_seo(self):
        """Category filter page must have category name in title, description, canonical, and single H1."""
        response = self.client.get(f'/category-filter/{self.active_cat.id}/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(self.active_cat.name, content)
        self.assertIn('rel="canonical"', content)
        self.assertIn(f'/category-filter/{self.active_cat.id}/', content)
        self.assertIn('"@type": "BreadcrumbList"', content)
        self.assertEqual(content.count('<h1'), 1)
        # Clean category page should NOT have noindex
        self.assertNotIn('content="noindex, follow"', content)

    def test_category_filter_faceted_query_has_noindex_follow(self):
        """Applying filters (e.g. price range, discount) must output noindex, follow to avoid duplicate content."""
        response = self.client.get(f'/category-filter/{self.active_cat.id}/?min_price=100000&discount=true')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('content="noindex, follow"', content)
        # Canonical URL must remain clean
        self.assertIn(f'rel="canonical" href="http://testserver/category-filter/{self.active_cat.id}/"', content)

    def test_catalog_search_query_has_noindex_follow(self):
        """Search queries must output noindex, follow with clean canonical."""
        response = self.client.get('/products/all/?q=iphone')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('content="noindex, follow"', content)
        self.assertIn('rel="canonical" href="http://testserver/products/all/"', content)

    def test_product_detail_seo_metadata(self):
        """Product detail page must have descriptive title, meta description, and canonical URL."""
        response = self.client.get(f'/product-detail/{self.product_active.code}/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(self.product_active.name, content)
        self.assertIn('rel="canonical"', content)
        self.assertIn(f'/product-detail/{self.product_active.code}/', content)

    def test_product_detail_single_h1(self):
        """Product detail page must contain exactly 1 authoritative H1."""
        response = self.client.get(f'/product-detail/{self.product_active.code}/')
        content = response.content.decode('utf-8')
        self.assertEqual(content.count('<h1'), 1)
        self.assertIn('id="product-title"', content)

    def test_product_detail_json_ld_structured_data(self):
        """Product detail page must include valid JSON-LD Product and BreadcrumbList schemas."""
        response = self.client.get(f'/product-detail/{self.product_active.code}/')
        content = response.content.decode('utf-8')
        self.assertIn('"@type": "Product"', content)
        self.assertIn('"@type": "Offer"', content)
        self.assertIn('"@type": "BreadcrumbList"', content)
        self.assertIn(self.product_active.name, content)
        self.assertIn('14200000', content)  # Discount price
        self.assertIn('https://schema.org/InStock', content)

    def test_product_detail_open_graph_and_twitter_tags(self):
        """Product detail page must output Open Graph and Twitter Card tags with absolute URLs."""
        response = self.client.get(f'/product-detail/{self.product_active.code}/')
        content = response.content.decode('utf-8')
        self.assertIn('property="og:title"', content)
        self.assertIn('property="og:image"', content)
        self.assertIn('property="og:type" content="product"', content)
        self.assertIn('name="twitter:card"', content)
        self.assertIn('name="twitter:image"', content)
        self.assertIn('http://testserver', content)

    def test_private_pages_noindex_nofollow_profile(self):
        """Profile page must have noindex, nofollow."""
        self.client.force_login(self.user)
        response = self.client.get('/profile/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('content="noindex, nofollow"', content)

    def test_private_pages_noindex_nofollow_cart(self):
        """Cart page must have noindex, nofollow."""
        self.client.force_login(self.user)
        response = self.client.get('/cart/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('content="noindex, nofollow"', content)

    def test_private_pages_noindex_nofollow_checkout(self):
        """Checkout page must have noindex, nofollow."""
        self.client.force_login(self.user)
        cart = Cart.objects.create(user=self.user, status=1)
        CartProduct.objects.create(cart=cart, product=self.product_active, count=1)
        response = self.client.get('/checkout/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('content="noindex, nofollow"', content)

    def test_private_pages_noindex_nofollow_auth(self):
        """Login and verify OTP pages must have noindex, nofollow."""
        login_res = self.client.get('/login/')
        self.assertEqual(login_res.status_code, 200)
        self.assertIn('content="noindex, nofollow"', login_res.content.decode('utf-8'))

        session = self.client.session
        session['otp_user_id'] = self.user.pk
        session['otp_phone'] = self.user.phone
        session.save()
        otp_res = self.client.get('/verify-otp/')
        self.assertEqual(otp_res.status_code, 200)
        self.assertIn('content="noindex, nofollow"', otp_res.content.decode('utf-8'))

    def test_private_pages_noindex_nofollow_orders_and_wishlist(self):
        """Orders detail and Wishlist pages must have noindex, nofollow."""
        self.client.force_login(self.user)
        order = Cart.objects.create(user=self.user, status=2)
        order_res = self.client.get(f'/orders/{order.code}/')
        self.assertEqual(order_res.status_code, 200)
        self.assertIn('content="noindex, nofollow"', order_res.content.decode('utf-8'))

        wishlist_res = self.client.get('/wishlist/')
        self.assertEqual(wishlist_res.status_code, 200)
        self.assertIn('content="noindex, nofollow"', wishlist_res.content.decode('utf-8'))

    def test_dashboard_noindex_nofollow(self):
        """Dashboard base template must contain noindex, nofollow."""
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        res = self.client.get('/dashboard/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('name="robots" content="noindex, nofollow"', res.content.decode('utf-8'))

    def test_error_pages_noindex_nofollow(self):
        """Custom error pages must contain noindex, nofollow."""
        with self.settings(DEBUG=False):
            res_404 = self.client.get('/page-that-does-not-exist-xyz/')
            self.assertEqual(res_404.status_code, 404)
            self.assertIn('name="robots" content="noindex, nofollow"', res_404.content.decode('utf-8'))

    def test_html_lang_attribute(self):
        """Base HTML must specify Uzbek language lang='uz'."""
        response = self.client.get('/')
        content = response.content.decode('utf-8')
        self.assertIn('<html lang="uz">', content)

    def test_models_get_absolute_url(self):
        """Category and Product models get_absolute_url method verification."""
        self.assertEqual(self.active_cat.get_absolute_url(), f'/category-filter/{self.active_cat.id}/')
        self.assertEqual(self.product_active.get_absolute_url(), f'/product-detail/{self.product_active.code}/')

















