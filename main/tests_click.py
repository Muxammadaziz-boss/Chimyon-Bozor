import hashlib
from decimal import Decimal
from django.test import TestCase, Client, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse

from main import models
from main.services.payment import PaymentManager
from main.services.payment.base import PaymentConfigurationError
from main.services.payment.click import ClickPaymentProvider

User = get_user_model()


class ClickPaymentIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()

        self.customer = User.objects.create_user(
            username='clickuser',
            password='password123',
            phone='+998901112233',
            address='Toshkent sh., Yunusobod'
        )

        self.category = models.Category.objects.create(name='Texnika')
        self.product = models.Product.objects.create(
            name='Test Tovari',
            category=self.category,
            price=889000.00,
            count=10
        )

        self.settings = models.SiteSettings.objects.create(
            site_name="Chimyon-bozor",
            prepayment_enabled=True,
            prepayment_percent=30,
            allow_cash_balance=True,
            allow_online_balance_payment=True
        )

        self.cart = models.Cart.objects.create(user=self.customer, status=1)
        self.cart_product = models.CartProduct.objects.create(
            cart=self.cart,
            product=self.product,
            count=1
        )

    # -------------------------------------------------------------
    # 1. Configuration & Placeholder Validation Tests
    # -------------------------------------------------------------
    @override_settings(CLICK_SERVICE_ID='', CLICK_MERCHANT_ID='', CLICK_SECRET_KEY='')
    def test_click_unconfigured_credentials_raises_configuration_error(self):
        provider = ClickPaymentProvider()
        self.assertFalse(provider.is_configured())

        with self.assertRaises(PaymentConfigurationError):
            provider.validate_configuration()

        with self.assertRaises(PaymentConfigurationError):
            PaymentManager.create_payment(order=self.cart, provider_name='click')

    @override_settings(
        CLICK_SERVICE_ID='test_service_id',
        CLICK_MERCHANT_ID='test_merchant_id',
        CLICK_SECRET_KEY='test_click_secret'
    )
    def test_click_placeholder_credentials_raises_configuration_error(self):
        provider = ClickPaymentProvider()
        self.assertFalse(provider.is_configured())

        with self.assertRaises(PaymentConfigurationError):
            provider.validate_configuration()

    @override_settings(
        CLICK_SERVICE_ID='54321',
        CLICK_MERCHANT_ID='12345',
        CLICK_SECRET_KEY='live_secret_key_abcdef'
    )
    def test_click_valid_credentials_is_configured(self):
        provider = ClickPaymentProvider()
        self.assertTrue(provider.is_configured())
        # Should not raise
        provider.validate_configuration()

    # -------------------------------------------------------------
    # 2. Redirect URL & Amount Formatting
    # -------------------------------------------------------------
    @override_settings(
        CLICK_SERVICE_ID='54321',
        CLICK_MERCHANT_ID='12345',
        CLICK_SECRET_KEY='live_secret_key_abcdef',
        SITE_URL='https://chimyon-bozor.uz'
    )
    def test_click_generate_checkout_url_formats_amount_and_params(self):
        payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click'
        )

        # 30% of 889,000 = 266,700.00
        self.assertEqual(payment.amount, Decimal('266700.00'))
        self.assertIn('service_id=54321', checkout_url)
        self.assertIn('merchant_id=12345', checkout_url)
        self.assertIn('amount=266700.00', checkout_url)
        self.assertIn(f'transaction_param={payment.code}', checkout_url)
        self.assertIn('https%3A%2F%2F', checkout_url)
        self.assertTrue(checkout_url.startswith('https://my.click.uz/services/pay?'))

    @override_settings(CLICK_SERVICE_ID='', CLICK_MERCHANT_ID='', CLICK_SECRET_KEY='')
    def test_checkout_view_catches_unconfigured_click_gracefully(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('checkout'), {
            'phone': '+998901112233',
            'address': 'Yunusobod',
            'provider': 'click'
        })

        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context['messages'])
        self.assertTrue(any("Click to'lov tizimi sozlamalari" in m.message for m in messages_list))

    # -------------------------------------------------------------
    # 3. Webhook Prepare Logic & Signature Check
    # -------------------------------------------------------------
    @override_settings(
        CLICK_SERVICE_ID='54321',
        CLICK_MERCHANT_ID='12345',
        CLICK_SECRET_KEY='live_secret_key_abcdef'
    )
    def test_click_prepare_webhook_success(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='click')
        provider = ClickPaymentProvider()

        sign_time = '2026-08-15 12:00:00'
        # prepare text: click_trans_id + service_id + secret_key + merchant_trans_id + amount + action + sign_time
        text = f"9988776654321live_secret_key_abcdef{payment.code}{payment.amount:.2f}0{sign_time}"
        sign_str = hashlib.md5(text.encode('utf-8')).hexdigest()

        req = self.factory.post('/payments/webhook/click/', data={
            'click_trans_id': '99887766',
            'service_id': '54321',
            'merchant_trans_id': str(payment.code),
            'amount': f"{payment.amount:.2f}",
            'action': '0',
            'error': '0',
            'error_note': 'Success',
            'sign_time': sign_time,
            'sign_string': sign_str
        })

        response = provider.handle_webhook(req)
        self.assertEqual(response.status_code, 200)
        import json
        res_data = json.loads(response.content)
        self.assertEqual(res_data['error'], ClickPaymentProvider.ERROR_SUCCESS)
        self.assertEqual(res_data['merchant_prepare_id'], payment.id)

        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.INITIATED)
        self.assertEqual(payment.transaction_id, '99887766')

    @override_settings(
        CLICK_SERVICE_ID='54321',
        CLICK_MERCHANT_ID='12345',
        CLICK_SECRET_KEY='live_secret_key_abcdef'
    )
    def test_click_prepare_webhook_invalid_signature_rejected(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='click')
        provider = ClickPaymentProvider()

        req = self.factory.post('/payments/webhook/click/', data={
            'click_trans_id': '99887766',
            'service_id': '54321',
            'merchant_trans_id': str(payment.code),
            'amount': f"{payment.amount:.2f}",
            'action': '0',
            'sign_time': '2026-08-15 12:00:00',
            'sign_string': 'invalid_md5_signature'
        })

        response = provider.handle_webhook(req)
        import json
        res_data = json.loads(response.content)
        self.assertEqual(res_data['error'], ClickPaymentProvider.ERROR_SIGN_CHECK_FAILED)

    @override_settings(
        CLICK_SERVICE_ID='54321',
        CLICK_MERCHANT_ID='12345',
        CLICK_SECRET_KEY='live_secret_key_abcdef'
    )
    def test_click_prepare_webhook_amount_mismatch_rejected(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='click')
        provider = ClickPaymentProvider()

        sign_time = '2026-08-15 12:00:00'
        wrong_amount = '100000.00'
        text = f"9988776654321live_secret_key_abcdef{payment.code}{wrong_amount}0{sign_time}"
        sign_str = hashlib.md5(text.encode('utf-8')).hexdigest()

        req = self.factory.post('/payments/webhook/click/', data={
            'click_trans_id': '99887766',
            'service_id': '54321',
            'merchant_trans_id': str(payment.code),
            'amount': wrong_amount,
            'action': '0',
            'sign_time': sign_time,
            'sign_string': sign_str
        })

        response = provider.handle_webhook(req)
        import json
        res_data = json.loads(response.content)
        self.assertEqual(res_data['error'], ClickPaymentProvider.ERROR_INVALID_AMOUNT)

    # -------------------------------------------------------------
    # 4. Webhook Complete Logic & Idempotency
    # -------------------------------------------------------------
    @override_settings(
        CLICK_SERVICE_ID='54321',
        CLICK_MERCHANT_ID='12345',
        CLICK_SECRET_KEY='live_secret_key_abcdef'
    )
    def test_click_complete_webhook_success_and_idempotency(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='click')
        provider = ClickPaymentProvider()

        sign_time = '2026-08-15 12:00:00'
        # complete text: click_trans_id + service_id + secret_key + merchant_trans_id + merchant_prepare_id + amount + action + sign_time
        text = f"1122334454321live_secret_key_abcdef{payment.code}1{payment.amount:.2f}1{sign_time}"
        sign_str = hashlib.md5(text.encode('utf-8')).hexdigest()

        click_data = {
            'click_trans_id': '11223344',
            'service_id': '54321',
            'merchant_trans_id': str(payment.code),
            'merchant_prepare_id': '1',
            'amount': f"{payment.amount:.2f}",
            'action': '1',
            'error': '0',
            'error_note': 'Success',
            'sign_time': sign_time,
            'sign_string': sign_str
        }

        # 1st execution
        req1 = self.factory.post('/payments/webhook/click/', data=click_data)
        res1 = provider.handle_webhook(req1)
        self.assertEqual(res1.status_code, 200)

        payment.refresh_from_db()
        self.cart.refresh_from_db()
        self.product.refresh_from_db()

        self.assertEqual(payment.status, models.Payment.Status.PAID)
        self.assertEqual(self.cart.status, 2)  # Accepted
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)
        self.assertEqual(self.product.count, 9)  # 10 - 1 = 9

        # 2nd execution (duplicate webhook - idempotency test)
        req2 = self.factory.post('/payments/webhook/click/', data=click_data)
        res2 = provider.handle_webhook(req2)
        self.assertEqual(res2.status_code, 200)

        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 9)  # Still 9, no duplicate deduction!

    # -------------------------------------------------------------
    # 5. Full Prepayment & Balance Settlement Cycle (889,000 UZS -> 266,700 UZS + 622,300 UZS)
    # -------------------------------------------------------------
    @override_settings(
        CLICK_SERVICE_ID='54321',
        CLICK_MERCHANT_ID='12345',
        CLICK_SECRET_KEY='live_secret_key_abcdef'
    )
    def test_click_partial_prepayment_889000_30percent_flow(self):
        # Step A: Prepayment 30%
        financials = PaymentManager.calculate_order_financials(self.cart)
        self.assertEqual(financials['grand_total'], Decimal('889000.00'))
        self.assertEqual(financials['prepayment_amount'], Decimal('266700.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('889000.00'))

        prepay_payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click'
        )

        self.assertEqual(prepay_payment.amount, Decimal('266700.00'))
        self.assertEqual(prepay_payment.purpose, models.Payment.Purpose.PREPAYMENT)

        # Complete Prepayment
        provider = ClickPaymentProvider()
        sign_time = '2026-08-15 12:30:00'
        text = f"5566778854321live_secret_key_abcdef{prepay_payment.code}1{prepay_payment.amount:.2f}1{sign_time}"
        sign_str = hashlib.md5(text.encode('utf-8')).hexdigest()

        req = self.factory.post('/payments/webhook/click/', data={
            'click_trans_id': '55667788',
            'service_id': '54321',
            'merchant_trans_id': str(prepay_payment.code),
            'merchant_prepare_id': '1',
            'amount': f"{prepay_payment.amount:.2f}",
            'action': '1',
            'error': '0',
            'sign_time': sign_time,
            'sign_string': sign_str
        })
        provider.handle_webhook(req)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)
        self.assertEqual(self.cart.paid_amount, Decimal('266700.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('622300.00'))

        # Step B: Balance Settlement (622,300 UZS) via Click online
        bal_payment, bal_checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            purpose=models.Payment.Purpose.BALANCE
        )

        self.assertEqual(bal_payment.amount, Decimal('622300.00'))
        self.assertEqual(bal_payment.purpose, models.Payment.Purpose.BALANCE)
        self.assertIn('amount=622300.00', bal_checkout_url)

        # Complete Balance Payment
        sign_time2 = '2026-08-15 13:00:00'
        text2 = f"9911223354321live_secret_key_abcdef{bal_payment.code}1{bal_payment.amount:.2f}1{sign_time2}"
        sign_str2 = hashlib.md5(text2.encode('utf-8')).hexdigest()

        req2 = self.factory.post('/payments/webhook/click/', data={
            'click_trans_id': '99112233',
            'service_id': '54321',
            'merchant_trans_id': str(bal_payment.code),
            'merchant_prepare_id': '1',
            'amount': f"{bal_payment.amount:.2f}",
            'action': '1',
            'error': '0',
            'sign_time': sign_time2,
            'sign_string': sign_str2
        })
        provider.handle_webhook(req2)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.FULLY_PAID)
        self.assertEqual(self.cart.paid_amount, Decimal('889000.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('0.00'))
        self.assertTrue(self.cart.is_fully_paid)
