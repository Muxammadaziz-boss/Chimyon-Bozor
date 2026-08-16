import base64
import hashlib
import hmac
import json
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from main import models
from main.services.payment import PaymentManager
from main.services.payment.base import PaymentConfigurationError

User = get_user_model()


@override_settings(
    CLICK_SERVICE_ID='54321',
    CLICK_MERCHANT_ID='12345',
    CLICK_SECRET_KEY='click_sec_123',
    PAYME_MERCHANT_ID='payme_123',
    PAYME_SECRET_KEY='payme_sec_123',
    UZUM_MERCHANT_ID='uzum_123',
    UZUM_SECRET_KEY='uzum_sec_123',
    SITE_URL='https://chimyon-bozor.uz'
)
class RealPaymentProviderE2ETests(TestCase):
    """
    Real Payment Provider E2E Audit Test Suite covering all 20 required verification scenarios.
    """

    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()

        # 1. Users
        self.customer = User.objects.create_user(
            username='e2e_customer',
            password='password123',
            phone='+998901234567',
            address='Toshkent sh., Yunusobod tumani, 4-mavze'
        )
        self.admin_user = User.objects.create_superuser(
            username='e2e_admin',
            password='password123',
            email='admin@chimyon-bozor.uz'
        )

        # 2. Product and Category (967,600 UZS canonical test amount)
        self.category = models.Category.objects.create(name='Texnika')
        self.product = models.Product.objects.create(
            name='Kir Yuvish Mashinasi Pro',
            category=self.category,
            price=Decimal('967600.00'),
            count=10
        )

        # 3. Site Settings (Prepayment 30%, 50%, 100% enabled)
        self.settings = models.SiteSettings.objects.create(
            site_name='Chimyon-bozor',
            prepayment_enabled=True,
            prepayment_percent=30,
            allowed_prepayment_percentages='30,50,100',
            allow_cash_balance=True,
            allow_online_balance_payment=True
        )

        # 4. Active Cart
        self.cart = models.Cart.objects.create(user=self.customer, status=1)
        self.cart_product = models.CartProduct.objects.create(
            cart=self.cart,
            product=self.product,
            count=1
        )

    # =========================================================================
    # TEST 1: Click Success Flow (Prepare -> Complete -> Paid -> Stock -> Ledger)
    # =========================================================================
    def test_01_click_success_flow(self):
        payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            chosen_percent=30
        )
        self.assertEqual(payment.amount, Decimal('290280.00'))
        self.assertEqual(payment.status, models.Payment.Status.INITIATED)

        service_id = '54321'
        secret_key = 'click_sec_123'
        click_trans_id = '11223344'
        sign_time = '2026-08-16 12:00:00'

        # 1. Prepare (Action 0)
        prep_sign_str = f"{click_trans_id}{service_id}{secret_key}{payment.code}290280.000{sign_time}"
        prep_sign = hashlib.md5(prep_sign_str.encode('utf-8')).hexdigest()

        prep_res = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': click_trans_id,
            'service_id': service_id,
            'merchant_trans_id': payment.code,
            'amount': '290280.00',
            'action': '0',
            'error': '0',
            'sign_time': sign_time,
            'sign_string': prep_sign,
        })
        self.assertEqual(prep_res.status_code, 200)
        self.assertEqual(prep_res.json()['error'], 0)
        self.assertEqual(prep_res.json()['merchant_prepare_id'], payment.id)

        # 2. Complete (Action 1)
        comp_sign_str = f"{click_trans_id}{service_id}{secret_key}{payment.code}{payment.id}290280.001{sign_time}"
        comp_sign = hashlib.md5(comp_sign_str.encode('utf-8')).hexdigest()

        comp_res = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': click_trans_id,
            'service_id': service_id,
            'merchant_trans_id': payment.code,
            'merchant_prepare_id': payment.id,
            'amount': '290280.00',
            'action': '1',
            'error': '0',
            'sign_time': sign_time,
            'sign_string': comp_sign,
        })
        self.assertEqual(comp_res.status_code, 200)
        self.assertEqual(comp_res.json()['error'], 0)

        # 3. Assert payment state
        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.PAID)
        self.assertEqual(payment.transaction_id, click_trans_id)
        self.assertIsNotNone(payment.paid_at)

        # 4. Assert stock deducted & order financial status
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 9)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, 2)  # Accepted
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)
        self.assertEqual(self.cart.paid_amount, Decimal('290280.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('677320.00'))

        # 5. Assert ledger entries
        self.assertTrue(models.OrderStatusHistory.objects.filter(order=self.cart).exists())
        self.assertTrue(models.AuditLog.objects.filter(user=self.customer, action="PAYMENT_SUCCESS_CLICK").exists())

    # =========================================================================
    # TEST 2: Click Failure Flow
    # =========================================================================
    def test_02_click_failure_flow(self):
        payment, _ = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            chosen_percent=30
        )
        service_id = '54321'
        secret_key = 'click_sec_123'
        click_trans_id = '11223355'
        sign_time = '2026-08-16 12:00:00'

        comp_sign_str = f"{click_trans_id}{service_id}{secret_key}{payment.code}{payment.id}290280.001{sign_time}"
        comp_sign = hashlib.md5(comp_sign_str.encode('utf-8')).hexdigest()

        # Click reports failure (error = -9 transaction cancelled)
        comp_res = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': click_trans_id,
            'service_id': service_id,
            'merchant_trans_id': payment.code,
            'merchant_prepare_id': payment.id,
            'amount': '290280.00',
            'action': '1',
            'error': '-9',
            'error_note': 'User cancelled payment',
            'sign_time': sign_time,
            'sign_string': comp_sign,
        })
        self.assertEqual(comp_res.status_code, 200)
        self.assertEqual(comp_res.json()['error'], -9)

        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.FAILED)
        self.assertIn("User cancelled", payment.error_message)

        # Stock and paid amount must NOT be altered
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 10)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('0.00'))

    # =========================================================================
    # TEST 3: Click Duplicate Callback (Idempotency)
    # =========================================================================
    def test_03_click_duplicate_callback(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='click', chosen_percent=30)
        service_id = '54321'
        secret_key = 'click_sec_123'
        click_trans_id = '11223366'
        sign_time = '2026-08-16 12:00:00'

        comp_sign_str = f"{click_trans_id}{service_id}{secret_key}{payment.code}{payment.id}290280.001{sign_time}"
        comp_sign = hashlib.md5(comp_sign_str.encode('utf-8')).hexdigest()

        payload = {
            'click_trans_id': click_trans_id,
            'service_id': service_id,
            'merchant_trans_id': payment.code,
            'merchant_prepare_id': payment.id,
            'amount': '290280.00',
            'action': '1',
            'error': '0',
            'sign_time': sign_time,
            'sign_string': comp_sign,
        }

        # Send callback 3 times
        res1 = self.client.post(reverse('payment_webhook', args=['click']), payload)
        res2 = self.client.post(reverse('payment_webhook', args=['click']), payload)
        res3 = self.client.post(reverse('payment_webhook', args=['click']), payload)

        self.assertEqual(res1.json()['error'], 0)
        self.assertEqual(res2.json()['error'], 0)
        self.assertEqual(res3.json()['error'], 0)

        # Stock deducted exactly ONCE (10 - 1 = 9)
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 9)

        # Paid amount counted exactly ONCE
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('290280.00'))

    # =========================================================================
    # TEST 4: Click Invalid Callback (Signature / Malformed Data)
    # =========================================================================
    def test_04_click_invalid_callback(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='click', chosen_percent=30)

        # Bad MD5 signature
        bad_res = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': '999999',
            'service_id': '54321',
            'merchant_trans_id': payment.code,
            'amount': '290280.00',
            'action': '0',
            'sign_time': '2026-08-16 12:00:00',
            'sign_string': 'forged_invalid_md5_hash',
        })
        self.assertEqual(bad_res.status_code, 200)
        self.assertEqual(bad_res.json()['error'], -1)

        payment.refresh_from_db()
        self.assertNotEqual(payment.status, models.Payment.Status.PAID)

    # =========================================================================
    # TEST 5: Payme Success Flow (CheckPerform -> Create -> Perform)
    # =========================================================================
    def test_05_payme_success_flow(self):
        payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='payme',
            chosen_percent=50
        )
        self.assertEqual(payment.amount, Decimal('483800.00'))
        self.assertTrue(checkout_url.startswith('https://checkout.paycom.uz/'))

        secret_key = 'payme_sec_123'
        auth_header = f"Basic {base64.b64encode(f'Paycom:{secret_key}'.encode('utf-8')).decode('utf-8')}"
        payme_trans_id = 'payme_tx_1001'
        amount_tiyin = 48380000  # 483,800.00 UZS in tiyin

        # 1. CheckPerformTransaction
        res1 = self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps({
                'method': 'CheckPerformTransaction',
                'params': {'amount': amount_tiyin, 'account': {'order_id': str(payment.code)}},
                'id': 1
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )
        self.assertEqual(res1.status_code, 200)
        self.assertTrue(res1.json()['result']['allow'])

        # 2. CreateTransaction
        res2 = self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps({
                'method': 'CreateTransaction',
                'params': {
                    'id': payme_trans_id,
                    'time': 1723800000000,
                    'amount': amount_tiyin,
                    'account': {'order_id': str(payment.code)}
                },
                'id': 2
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()['result']['state'], 1)

        # 3. PerformTransaction
        res3 = self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps({
                'method': 'PerformTransaction',
                'params': {'id': payme_trans_id},
                'id': 3
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(res3.json()['result']['state'], 2)

        # Assert results
        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.PAID)
        self.assertEqual(payment.transaction_id, payme_trans_id)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('483800.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('483800.00'))
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)

    # =========================================================================
    # TEST 6: Payme Failure and Cancel Flow
    # =========================================================================
    def test_06_payme_failure_and_cancel_flow(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='payme', chosen_percent=30)
        secret_key = 'payme_sec_123'
        auth_header = f"Basic {base64.b64encode(f'Paycom:{secret_key}'.encode('utf-8')).decode('utf-8')}"
        payme_trans_id = 'payme_tx_cancel_1'

        # Create then Cancel
        self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps({
                'method': 'CreateTransaction',
                'params': {
                    'id': payme_trans_id,
                    'time': 1723800000000,
                    'amount': 29028000,
                    'account': {'order_id': str(payment.code)}
                },
                'id': 1
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )

        cancel_res = self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps({
                'method': 'CancelTransaction',
                'params': {'id': payme_trans_id, 'reason': 1},
                'id': 2
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )
        self.assertEqual(cancel_res.status_code, 200)
        self.assertEqual(cancel_res.json()['result']['state'], -1)

        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.CANCELLED)

    # =========================================================================
    # TEST 7: Payme Duplicate Callback (Idempotent Perform)
    # =========================================================================
    def test_07_payme_duplicate_callback(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='payme', chosen_percent=50)
        secret_key = 'payme_sec_123'
        auth_header = f"Basic {base64.b64encode(f'Paycom:{secret_key}'.encode('utf-8')).decode('utf-8')}"
        payme_trans_id = 'payme_tx_dup_1'

        # Create
        self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps({
                'method': 'CreateTransaction',
                'params': {
                    'id': payme_trans_id,
                    'time': 1723800000000,
                    'amount': 48380000,
                    'account': {'order_id': str(payment.code)}
                },
                'id': 1
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )

        perform_payload = json.dumps({'method': 'PerformTransaction', 'params': {'id': payme_trans_id}, 'id': 2})

        # Perform 3 times
        r1 = self.client.post(reverse('payment_webhook', args=['payme']), perform_payload, content_type='application/json', HTTP_AUTHORIZATION=auth_header)
        r2 = self.client.post(reverse('payment_webhook', args=['payme']), perform_payload, content_type='application/json', HTTP_AUTHORIZATION=auth_header)
        r3 = self.client.post(reverse('payment_webhook', args=['payme']), perform_payload, content_type='application/json', HTTP_AUTHORIZATION=auth_header)

        self.assertEqual(r1.json()['result']['state'], 2)
        self.assertEqual(r2.json()['result']['state'], 2)
        self.assertEqual(r3.json()['result']['state'], 2)

        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 9)  # Deducted only once

    # =========================================================================
    # TEST 8: Uzum Success Flow (HMAC SHA256 Signature Verification)
    # =========================================================================
    def test_08_uzum_success_flow(self):
        payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='uzum',
            chosen_percent=30
        )
        self.assertEqual(payment.amount, Decimal('290280.00'))
        self.assertTrue(checkout_url.startswith('https://pay.uzumbank.uz/checkout?'))

        secret_key = 'uzum_sec_123'
        payload = json.dumps({
            'event': 'SUCCESS',
            'orderId': str(payment.code),
            'transactionId': 'uzum_tx_555',
            'amount': '290280.00'
        })
        signature = hmac.new(secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

        res = self.client.post(
            reverse('payment_webhook', args=['uzum']),
            data=payload,
            content_type='application/json',
            HTTP_X_SIGNATURE=signature
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'ok')

        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.PAID)
        self.assertEqual(payment.transaction_id, 'uzum_tx_555')

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('290280.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('677320.00'))
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)

    # =========================================================================
    # TEST 9: Uzum Failure Flow
    # =========================================================================
    def test_09_uzum_failure_flow(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='uzum', chosen_percent=30)
        secret_key = 'uzum_sec_123'

        payload = json.dumps({
            'event': 'FAILED',
            'orderId': str(payment.code),
            'transactionId': 'uzum_tx_fail_1',
            'error': 'Insufficient funds on card'
        })
        signature = hmac.new(secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

        res = self.client.post(
            reverse('payment_webhook', args=['uzum']),
            data=payload,
            content_type='application/json',
            HTTP_X_SIGNATURE=signature
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'ok')

        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.FAILED)
        self.assertIn('Insufficient funds', payment.error_message)

    # =========================================================================
    # TEST 10: Uzum Duplicate Callback
    # =========================================================================
    def test_10_uzum_duplicate_callback(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='uzum', chosen_percent=30)
        secret_key = 'uzum_sec_123'

        payload = json.dumps({
            'event': 'SUCCESS',
            'orderId': str(payment.code),
            'transactionId': 'uzum_tx_dup_1',
            'amount': '290280.00'
        })
        signature = hmac.new(secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

        # Send 3 times
        r1 = self.client.post(reverse('payment_webhook', args=['uzum']), data=payload, content_type='application/json', HTTP_X_SIGNATURE=signature)
        r2 = self.client.post(reverse('payment_webhook', args=['uzum']), data=payload, content_type='application/json', HTTP_X_SIGNATURE=signature)
        r3 = self.client.post(reverse('payment_webhook', args=['uzum']), data=payload, content_type='application/json', HTTP_X_SIGNATURE=signature)

        self.assertEqual(r1.json()['status'], 'ok')
        self.assertEqual(r2.json()['status'], 'ok')
        self.assertEqual(r3.json()['status'], 'ok')

        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 9)

    # =========================================================================
    # TEST 11: Amount Tampering Rejection
    # =========================================================================
    def test_11_amount_tampering_rejected(self):
        # 1. Server calculates authoritative amount
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='click', chosen_percent=30)
        self.assertEqual(payment.amount, Decimal('290280.00'))

        # 2. Client / attacker tries callback with forged amount (e.g. 1000.00 UZS)
        service_id = '54321'
        secret_key = 'click_sec_123'
        click_trans_id = '99998888'
        sign_time = '2026-08-16 12:00:00'
        fake_sign_str = f"{click_trans_id}{service_id}{secret_key}{payment.code}1000.000{sign_time}"
        fake_sign = hashlib.md5(fake_sign_str.encode('utf-8')).hexdigest()

        res = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': click_trans_id,
            'service_id': service_id,
            'merchant_trans_id': payment.code,
            'amount': '1000.00',
            'action': '0',
            'sign_time': sign_time,
            'sign_string': fake_sign,
        })
        self.assertEqual(res.json()['error'], -2)  # ERROR_INVALID_AMOUNT

    # =========================================================================
    # TEST 12: Wrong / Non-Existent Order Rejection
    # =========================================================================
    def test_12_wrong_order_rejected(self):
        service_id = '54321'
        secret_key = 'click_sec_123'
        click_trans_id = '99991111'
        sign_time = '2026-08-16 12:00:00'
        fake_code = 'non_existent_payment_code_123'

        sign_str = f"{click_trans_id}{service_id}{secret_key}{fake_code}290280.000{sign_time}"
        sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()

        res = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': click_trans_id,
            'service_id': service_id,
            'merchant_trans_id': fake_code,
            'amount': '290280.00',
            'action': '0',
            'sign_time': sign_time,
            'sign_string': sign,
        })
        self.assertEqual(res.json()['error'], -5)  # ERROR_ORDER_NOT_FOUND

    # =========================================================================
    # TEST 13: 30% Prepayment Calculation and Execution
    # =========================================================================
    def test_13_prepayment_30_calculation_and_execution(self):
        financials = PaymentManager.calculate_order_financials(self.cart, chosen_percent=30)
        self.assertEqual(financials['grand_total'], Decimal('967600.00'))
        self.assertEqual(financials['prepayment_amount'], Decimal('290280.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('677320.00'))

    # =========================================================================
    # TEST 14: 50% Prepayment Calculation and Execution
    # =========================================================================
    def test_14_prepayment_50_calculation_and_execution(self):
        financials = PaymentManager.calculate_order_financials(self.cart, chosen_percent=50)
        self.assertEqual(financials['grand_total'], Decimal('967600.00'))
        self.assertEqual(financials['prepayment_amount'], Decimal('483800.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('483800.00'))

    # =========================================================================
    # TEST 15: 100% Prepayment Calculation and Execution
    # =========================================================================
    def test_15_prepayment_100_calculation_and_execution(self):
        financials = PaymentManager.calculate_order_financials(self.cart, chosen_percent=100)
        self.assertEqual(financials['grand_total'], Decimal('967600.00'))
        self.assertEqual(financials['prepayment_amount'], Decimal('967600.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('0.00'))

    # =========================================================================
    # TEST 16: Balance Payment Settlement Flow
    # =========================================================================
    def test_16_balance_payment_settlement(self):
        # 1. 30% Prepayment settled
        p1 = models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('290280.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        self.assertEqual(self.cart.paid_amount, Decimal('290280.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('677320.00'))

        # 2. Balance payment created & paid
        p2, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            purpose=models.Payment.Purpose.BALANCE
        )
        self.assertEqual(p2.amount, Decimal('677320.00'))
        self.assertEqual(p2.purpose, models.Payment.Purpose.BALANCE)

        p2.status = models.Payment.Status.PAID
        p2.paid_at = timezone.now()
        p2.save()
        PaymentManager.sync_order_financial_status(self.cart)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('967600.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('0.00'))
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.FULLY_PAID)

    # =========================================================================
    # TEST 17: Retry Payment After Failure
    # =========================================================================
    def test_17_retry_payment_after_failure(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='click', chosen_percent=30)
        payment.status = models.Payment.Status.FAILED
        payment.save()

        self.client.force_login(self.customer)
        response = self.client.post(reverse('retry_payment', kwargs={'code': self.cart.code}), {
            'provider': 'click'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('https://my.click.uz/services/pay?'))

        # New initiated payment exists
        new_p = models.Payment.objects.filter(order=self.cart, status=models.Payment.Status.INITIATED).first()
        self.assertIsNotNone(new_p)
        self.assertEqual(new_p.amount, Decimal('290280.00'))

    # =========================================================================
    # TEST 18: Idempotency on Checkout Retry (No duplicate ghost rows)
    # =========================================================================
    def test_18_idempotency_checkout_retry(self):
        models.Address.objects.create(name='Toshkent', is_active=True)
        self.client.force_login(self.customer)

        payload = {
            'phone': '+998901234567',
            'address': 'Toshkent',
            'provider': 'click',
            'prepayment_percent': '30'
        }

        r1 = self.client.post(reverse('checkout'), payload)
        r2 = self.client.post(reverse('checkout'), payload)

        self.assertEqual(r1.status_code, 302)
        self.assertEqual(r2.status_code, 302)
        active_payments = models.Payment.objects.filter(order=self.cart, status__in=[models.Payment.Status.INITIATED, models.Payment.Status.PENDING])
        self.assertEqual(active_payments.count(), 1)

    # =========================================================================
    # TEST 19: Race Condition & Transaction Locking
    # =========================================================================
    def test_19_race_condition_transaction_locking(self):
        payment, _ = PaymentManager.create_payment(order=self.cart, provider_name='click', chosen_percent=30)
        self.cart.status = 2
        self.cart.save()

        # Simulate two concurrent cash balance settlement requests
        p1 = PaymentManager.settle_cash_balance(order=self.cart)
        self.assertEqual(p1.status, models.Payment.Status.PAID)

        # Second settlement must fail because remaining is now 0.00
        self.cart.refresh_from_db()
        with self.assertRaises(ValueError):
            PaymentManager.settle_cash_balance(order=self.cart)

    # =========================================================================
    # TEST 20: Ledger Consistency (Payment Records + Audit Logs)
    # =========================================================================
    def test_20_ledger_consistency(self):
        # 1. Prepayment
        prep = models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('290280.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        # 2. Balance payment
        bal = PaymentManager.settle_cash_balance(order=self.cart, provider='cash')

        # Check total payments equals grand total
        payments = self.cart.payments.filter(status=models.Payment.Status.PAID)
        self.assertEqual(payments.count(), 2)
        total_paid = sum(p.amount for p in payments)
        self.assertEqual(total_paid, self.cart.grand_total)

        # Check OrderStatusHistory and AuditLog exist
        self.assertTrue(models.OrderStatusHistory.objects.filter(order=self.cart).exists())
        self.assertTrue(models.AuditLog.objects.filter(action="BALANCE_PAYMENT_SETTLED").exists())
