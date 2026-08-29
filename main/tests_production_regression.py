from decimal import Decimal
from datetime import timedelta
import logging
from unittest.mock import patch

from django.test import TestCase, Client
from django.utils import timezone
from django.db import IntegrityError, transaction
from django.core.management import call_command
from django.urls import reverse

from main import models
from main.services.payment import PaymentManager
from main.services.payment.payme import PaymePaymentProvider
from main.views import get_active_cart
from main.sms_service import send_sms_code


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


class PaymentRefundSecurityTests(TestCase):
    def setUp(self):
        self.user = models.User.objects.create_user(
            username='refund_test_user',
            password='TestPassword123!',
            phone='+998901234567',
            phone_verified=True,
        )
        self.category = models.Category.objects.create(name='Electronics')
        self.product = models.Product.objects.create(
            name='Test Phone',
            category=self.category,
            description='Test phone description',
            price=Decimal('100000.00'),
            count=5,
        )
        self.order = models.Cart.objects.create(user=self.user, status=2)
        models.CartProduct.objects.create(
            cart=self.order,
            product=self.product,
            count=1,
            unit_price_snapshot=Decimal('100000.00')
        )
        self.order.reserve_inventory()

    def test_full_refund_releases_inventory(self):
        payment = models.Payment.objects.create(
            order=self.order,
            amount=Decimal('100000.00'),
            provider=models.Payment.Provider.CLICK,
            purpose=models.Payment.Purpose.FULL,
            status=models.Payment.Status.PAID,
        )
        PaymentManager.sync_order_financial_status(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.financial_status, models.Cart.FinancialStatus.FULLY_PAID)

        # Refund full amount
        res = PaymentManager.refund_payment(payment, 100000.0, "Customer cancellation")
        self.assertTrue(res['success'])
        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.REFUNDED)
        self.assertEqual(payment.refund_amount, Decimal('100000.00'))

        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.order.inventory_status, models.Cart.InventoryStatus.RELEASED)
        self.assertEqual(self.product.count, 5)

    def test_partial_refund_and_over_refund_prevention(self):
        payment = models.Payment.objects.create(
            order=self.order,
            amount=Decimal('100000.00'),
            provider=models.Payment.Provider.PAYME,
            purpose=models.Payment.Purpose.FULL,
            status=models.Payment.Status.PAID,
        )
        # Partial refund 1: 40,000
        res1 = PaymentManager.refund_payment(payment, 40000.0, "Partial return")
        self.assertTrue(res1['success'])
        payment.refresh_from_db()
        self.assertEqual(payment.refund_amount, Decimal('40000.00'))

        # Over-refund attempt: 70,000 (exceeds remaining 60,000)
        res_over = PaymentManager.refund_payment(payment, 70000.0, "Too much")
        self.assertFalse(res_over['success'])
        payment.refresh_from_db()
        self.assertEqual(payment.refund_amount, Decimal('40000.00'))

        # Partial refund 2: exact remaining 60,000
        res2 = PaymentManager.refund_payment(payment, 60000.0, "Remaining balance refund")
        self.assertTrue(res2['success'])
        payment.refresh_from_db()
        self.assertEqual(payment.refund_amount, Decimal('100000.00'))
        self.assertEqual(payment.status, models.Payment.Status.REFUNDED)

    def test_negative_or_zero_refund_rejected(self):
        payment = models.Payment.objects.create(
            order=self.order,
            amount=Decimal('50000.00'),
            provider=models.Payment.Provider.UZUM,
            purpose=models.Payment.Purpose.FULL,
            status=models.Payment.Status.PAID,
        )
        res_zero = PaymentManager.refund_payment(payment, 0.0, "Zero refund")
        self.assertFalse(res_zero['success'])
        res_neg = PaymentManager.refund_payment(payment, -100.0, "Negative refund")
        self.assertFalse(res_neg['success'])

    def test_unpaid_payment_refund_rejected(self):
        payment = models.Payment.objects.create(
            order=self.order,
            amount=Decimal('50000.00'),
            provider=models.Payment.Provider.CLICK,
            purpose=models.Payment.Purpose.PREPAYMENT,
            status=models.Payment.Status.PENDING,
        )
        res = PaymentManager.refund_payment(payment, 50000.0, "Refund pending")
        self.assertFalse(res['success'])


class OTPSecurityTests(TestCase):
    def setUp(self):
        self.user = models.User.objects.create_user(
            username='otp_user',
            password='TestPassword123!',
            phone='+998909998877',
            phone_verified=False,
            is_active=False,
        )

    def test_otp_is_hashed_and_not_stored_plaintext(self):
        otp = models.OTPCode.objects.create(
            user=self.user,
            phone=self.user.phone,
            code='123456'
        )
        # Verify stored value is hashed
        self.assertNotEqual(otp.code, '123456')
        self.assertTrue(
            otp.code.startswith('pbkdf2_')
            or otp.code.startswith('argon2')
            or otp.code.startswith('bcrypt')
            or otp.code.startswith('md5$')
        )
        self.assertTrue(otp.check_code('123456'))
        self.assertFalse(otp.check_code('654321'))

    def test_otp_str_and_logs_do_not_leak_code(self):
        otp = models.OTPCode.objects.create(
            user=self.user,
            phone=self.user.phone,
            code='987654'
        )
        self.assertNotIn('987654', str(otp))

        with patch('main.sms_service.logger.info') as mock_log:
            send_sms_code(self.user.phone, '987654')
            mock_log.assert_called()
            for call in mock_log.call_args_list:
                formatted = call[0][0] % tuple(call[0][1:]) if len(call[0]) > 1 else call[0][0]
                self.assertNotIn('987654', formatted)

    def test_otp_lockout_after_five_attempts(self):
        otp = models.OTPCode.objects.create(
            user=self.user,
            phone=self.user.phone,
            code='555666'
        )
        client = Client()
        session = client.session
        session['otp_user_id'] = self.user.id
        session['otp_phone'] = self.user.phone
        session.save()

        # 4 wrong attempts
        for i in range(4):
            response = client.post(reverse('verify_otp'), {'otp_code': '000000'})
            self.assertEqual(response.status_code, 200)
            otp.refresh_from_db()
            self.assertEqual(otp.attempts, i + 1)
            self.assertFalse(otp.is_used)

        # 5th wrong attempt -> lockout & is_used set to True
        response = client.post(reverse('verify_otp'), {'otp_code': '000000'})
        self.assertEqual(response.status_code, 200)
        otp.refresh_from_db()
        self.assertTrue(otp.attempts >= 5)
        self.assertTrue(otp.is_used)

        # 6th attempt with correct code should fail because locked out
        response = client.post(reverse('verify_otp'), {'otp_code': '555666'})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_otp_resend_rate_limit(self):
        models.OTPCode.objects.create(
            user=self.user,
            phone=self.user.phone,
            code='111222'
        )
        client = Client()
        session = client.session
        session['otp_user_id'] = self.user.id
        session['otp_phone'] = self.user.phone
        session.save()

        # Immediate resend should be blocked by 60s cooldown
        response = client.post(reverse('resend_otp'))
        self.assertEqual(response.status_code, 302)
        # Should still have only 1 OTP code
        self.assertEqual(models.OTPCode.objects.filter(user=self.user).count(), 1)


class CartAndCartProductSecurityTests(TestCase):
    def setUp(self):
        self.user = models.User.objects.create_user(
            username='cart_user',
            password='TestPassword123!',
            phone='+998907776655',
            phone_verified=True,
        )
        self.category = models.Category.objects.create(name='Clothing')
        self.product = models.Product.objects.create(
            name='Test Shirt',
            category=self.category,
            description='Test shirt description',
            price=Decimal('50000.00'),
            count=3,
        )

    def test_unique_active_cart_per_user(self):
        cart1 = models.Cart.objects.create(user=self.user, status=1)
        self.assertIsNotNone(cart1)

        # Attempting to create another active cart (status=1) for same user must fail constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                models.Cart.objects.create(user=self.user, status=1)

        # get_active_cart helper must be safe
        active_cart = get_active_cart(self.user)
        self.assertEqual(active_cart.id, cart1.id)

    def test_unique_cart_product_and_count_gt_zero(self):
        cart = models.Cart.objects.create(user=self.user, status=1)
        cp1 = models.CartProduct.objects.create(cart=cart, product=self.product, count=1)
        self.assertIsNotNone(cp1)

        # Duplicate CartProduct must fail constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                models.CartProduct.objects.create(cart=cart, product=self.product, count=2)

        # Count <= 0 must fail constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                models.CartProduct.objects.create(cart=cart, product=models.Product.objects.create(
                    name='Another Shirt',
                    category=self.category,
                    price=Decimal('20000.00'),
                    count=1
                ), count=0)

    def test_add_to_cart_respects_stock(self):
        client = Client()
        client.force_login(self.user)

        # Add 2 items (stock is 3)
        res1 = client.post(reverse('add_to_cart', kwargs={'product_code': self.product.code}), {'quantity': 2})
        cart = get_active_cart(self.user)
        cp = models.CartProduct.objects.get(cart=cart, product=self.product)
        self.assertEqual(cp.count, 2)

        # Add 5 more items (should cap at stock 3)
        res2 = client.post(reverse('add_to_cart', kwargs={'product_code': self.product.code}), {'quantity': 5})
        cp.refresh_from_db()
        self.assertEqual(cp.count, 3)


class PaymeSecurityTests(TestCase):
    def setUp(self):
        self.provider = PaymePaymentProvider()
        self.user = models.User.objects.create_user(
            username='payme_user',
            password='TestPassword123!',
            phone='+998904443322',
            phone_verified=True,
        )
        self.order = models.Cart.objects.create(user=self.user, status=2)
        self.payment = models.Payment.objects.create(
            order=self.order,
            amount=Decimal('10000.00'),
            provider=models.Payment.Provider.PAYME,
            purpose=models.Payment.Purpose.FULL,
            status=models.Payment.Status.PENDING,
        )

    def test_create_transaction_hijacking_prevention(self):
        # Transaction 1 initializes payment
        params1 = {
            'id': 'payme_tx_11111',
            'time': 1700000000000,
            'amount': 1000000,  # 10000 UZS in tiyin
            'account': {'order_id': str(self.payment.code)}
        }
        res1 = self.provider._create_transaction(params1, 1)
        data1 = res1.content.decode()
        self.assertIn('"state": 1', data1)

        # Transaction 2 tries to bind to the same payment with a different transaction ID
        params2 = {
            'id': 'payme_tx_22222',
            'time': 1700000001000,
            'amount': 1000000,
            'account': {'order_id': str(self.payment.code)}
        }
        res2 = self.provider._create_transaction(params2, 2)
        data2 = res2.content.decode()
        self.assertIn('-31008', data2)

        # Retrying with the same original transaction ID should return idempotent result
        res_retry = self.provider._create_transaction(params1, 3)
        data_retry = res_retry.content.decode()
        self.assertIn('"state": 1', data_retry)


class ExpiredReservationsCleanupTests(TestCase):
    def test_cleanup_expired_reservations_command(self):
        user = models.User.objects.create_user(
            username='cleanup_user',
            password='TestPassword123!',
            phone='+998905554433',
            phone_verified=True,
        )
        category = models.Category.objects.create(name='Food')
        product = models.Product.objects.create(
            name='Test Bread',
            category=category,
            description='Test bread description',
            price=Decimal('5000.00'),
            count=10,
        )
        order = models.Cart.objects.create(user=user, status=2)
        models.CartProduct.objects.create(cart=order, product=product, count=3)
        order.reserve_inventory()
        product.refresh_from_db()
        self.assertEqual(product.count, 7)

        # Backdate the order to 20 minutes ago
        past_time = timezone.now() - timedelta(minutes=20)
        models.Cart.objects.filter(id=order.id).update(date=past_time, inventory_updated_at=past_time)

        # Run management command
        call_command('cleanup_expired_reservations', timeout=15)

        order.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(order.inventory_status, models.Cart.InventoryStatus.RELEASED)
        self.assertEqual(product.count, 10)


class DashboardAggregationTests(TestCase):
    def test_dashboard_views_aggregate_without_error(self):
        staff = models.User.objects.create_user(
            username='admin_user',
            password='TestPassword123!',
            is_staff=True,
            is_superuser=True,
        )
        client = Client()
        client.force_login(staff)

        res_index = client.get(reverse('d_index'))
        self.assertEqual(res_index.status_code, 200)

        res_analytics = client.get(reverse('d_analytics'))
        self.assertEqual(res_analytics.status_code, 200)

        res_payments = client.get(reverse('d_payments'))
        self.assertEqual(res_payments.status_code, 200)

        res_reports = client.get(reverse('d_reports'))
        self.assertEqual(res_reports.status_code, 200)


class PriceSnapshotAndOrderImmutabilityTests(TestCase):
    def test_product_price_change_does_not_alter_historical_order_snapshot(self):
        user = models.User.objects.create_user(
            username='snapshot_user',
            password='TestPassword123!',
            phone='+998901239988',
            phone_verified=True,
        )
        category = models.Category.objects.create(name='Snapshot Category')
        product = models.Product.objects.create(
            name='Historical Item',
            category=category,
            price=Decimal('50000.00'),
            count=10,
        )
        order = models.Cart.objects.create(user=user, status=2)
        cart_item = models.CartProduct.objects.create(
            cart=order,
            product=product,
            count=2,
            unit_price_snapshot=Decimal('50000.00')
        )
        self.assertEqual(cart_item.total_price, Decimal('100000.00'))

        # Later, merchant changes the product price to 80,000
        product.price = Decimal('80000.00')
        product.save(update_fields=['price'])

        # Historical order snapshot unit_price and total_price must remain 50,000 and 100,000
        cart_item.refresh_from_db()
        self.assertEqual(cart_item.unit_price, Decimal('50000.00'))
        self.assertEqual(cart_item.total_price, Decimal('100000.00'))


class FileUploadAndSecurityValidatorTests(TestCase):
    def test_image_validator_rejects_disallowed_extensions(self):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile
        from main.validators import validate_image_file

        # SVG or executable files must be rejected
        svg_file = SimpleUploadedFile("malicious.svg", b"<svg onload=alert(1)></svg>", content_type="image/svg+xml")
        with self.assertRaises(ValidationError):
            validate_image_file(svg_file)

        exe_file = SimpleUploadedFile("script.exe", b"MZ\x90\x00\x03\x00\x00\x00", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            validate_image_file(exe_file)

    def test_image_validator_rejects_path_traversal_filename(self):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile
        from main.validators import validate_image_file

        traversal_file = SimpleUploadedFile("../../etc/passwd.png", b"\x89PNG\r\n\x1a\n\x00\x00", content_type="image/png")
        with self.assertRaises(ValidationError):
            validate_image_file(traversal_file)


class SecurityHeadersAndCSRFTests(TestCase):
    def test_security_headers_middleware_present_in_responses(self):
        client = Client()
        response = client.get(reverse('index'))
        self.assertIn('Permissions-Policy', response)
        self.assertIn('Cross-Origin-Opener-Policy', response)
        self.assertEqual(response.get('X-Content-Type-Options'), 'nosniff')


