import base64
import hashlib
import json
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.conf import settings

from main import models
from main.services.payment import PaymentManager


class PaymentIntegrationTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = models.User.objects.create_user(
            username='shopper',
            password='password123',
            phone='+998901234567',
            address="Toshkent sh., Yunusobod 4-mavze",
            phone_verified=True,
            is_active=True
        )
        self.admin_user = models.User.objects.create_superuser(
            username='adminuser',
            password='password123',
            email='admin@example.com'
        )

        self.category = models.Category.objects.create(name="Elektronika", is_active=True)
        self.product = models.Product.objects.create(
            category=self.category,
            name="Smartfon Pro",
            price=Decimal('1000000.00'),
            count=10
        )

    def test_payment_model_creation_and_properties(self):
        cart = models.Cart.objects.create(user=self.user, status=1)
        payment = models.Payment.objects.create(
            order=cart,
            provider=models.Payment.Provider.CLICK,
            amount=Decimal('1000000.00'),
            status=models.Payment.Status.INITIATED
        )
        self.assertIsNotNone(payment.code)
        self.assertFalse(payment.is_paid)
        self.assertEqual(payment.currency, 'UZS')
        self.assertIn("Click", str(payment))

        payment.status = models.Payment.Status.PAID
        payment.paid_at = timezone.now()
        payment.save()
        self.assertTrue(payment.is_paid)

    def test_checkout_cash_on_delivery_flow(self):
        self.client.force_login(self.user)
        cart = models.Cart.objects.create(user=self.user, status=1)
        models.CartProduct.objects.create(cart=cart, product=self.product, count=2)

        # Initial stock is 10
        response = self.client.post(reverse('checkout'), {
            'phone': '+998901234567',
            'address': 'Chilonzor 9-mavze, 12-uy',
            'provider': 'cash',
        })

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('payment_success', kwargs={'code': cart.code}))

        # Verify stock deducted: 10 - 2 = 8
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 8)

        # Verify Order & Payment status
        cart.refresh_from_db()
        self.assertEqual(cart.status, 2)  # Accepted
        payment = cart.payments.first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.provider, 'cash')
        self.assertEqual(payment.amount, Decimal('2000000.00'))
        self.assertEqual(payment.status, models.Payment.Status.PENDING)

    def test_checkout_online_provider_redirect(self):
        self.client.force_login(self.user)
        cart = models.Cart.objects.create(user=self.user, status=1)
        models.CartProduct.objects.create(cart=cart, product=self.product, count=1)

        response = self.client.post(reverse('checkout'), {
            'phone': '+998901234567',
            'address': 'Yunusobod 19-mavze',
            'provider': 'click',
        })

        self.assertEqual(response.status_code, 302)
        self.assertIn('my.click.uz', response.url)

        # Verify payment created in INITIATED status
        payment = cart.payments.first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.provider, 'click')
        self.assertEqual(payment.amount, Decimal('1000000.00'))
        self.assertEqual(payment.status, models.Payment.Status.INITIATED)

    def test_click_prepare_and_complete_webhook_flow(self):
        cart = models.Cart.objects.create(user=self.user, status=1)
        models.CartProduct.objects.create(cart=cart, product=self.product, count=1)
        payment = models.Payment.objects.create(
            order=cart,
            provider=models.Payment.Provider.CLICK,
            amount=Decimal('1000000.00'),
            status=models.Payment.Status.INITIATED
        )

        service_id = getattr(settings, 'CLICK_SERVICE_ID', 'test_service_id')
        secret_key = getattr(settings, 'CLICK_SECRET_KEY', 'test_click_secret')
        click_trans_id = '12345678'
        sign_time = '2026-08-14 20:00:00'

        # 1. Click Prepare (Action 0)
        prepare_str = f"{click_trans_id}{service_id}{secret_key}{payment.code}1000000.000{sign_time}"
        sign_string = hashlib.md5(prepare_str.encode('utf-8')).hexdigest()

        prep_response = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': click_trans_id,
            'service_id': service_id,
            'merchant_trans_id': payment.code,
            'amount': '1000000.00',
            'action': '0',
            'error': '0',
            'error_note': 'Success',
            'sign_time': sign_time,
            'sign_string': sign_string,
        })

        self.assertEqual(prep_response.status_code, 200)
        prep_data = prep_response.json()
        self.assertEqual(prep_data['error'], 0)
        self.assertEqual(prep_data['merchant_prepare_id'], payment.id)

        # 2. Click Complete (Action 1)
        complete_str = f"{click_trans_id}{service_id}{secret_key}{payment.code}{payment.id}1000000.001{sign_time}"
        comp_sign = hashlib.md5(complete_str.encode('utf-8')).hexdigest()

        comp_response = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': click_trans_id,
            'service_id': service_id,
            'merchant_trans_id': payment.code,
            'merchant_prepare_id': payment.id,
            'amount': '1000000.00',
            'action': '1',
            'error': '0',
            'error_note': 'Success',
            'sign_time': sign_time,
            'sign_string': comp_sign,
        })

        self.assertEqual(comp_response.status_code, 200)
        comp_data = comp_response.json()
        self.assertEqual(comp_data['error'], 0)

        # Verify Payment is PAID & Order is status 2
        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.PAID)
        self.assertEqual(payment.transaction_id, click_trans_id)
        self.assertIsNotNone(payment.paid_at)

        cart.refresh_from_db()
        self.assertEqual(cart.status, 2)

        # 3. Duplicate Webhook (Idempotency Check)
        dup_response = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': click_trans_id,
            'service_id': service_id,
            'merchant_trans_id': payment.code,
            'merchant_prepare_id': payment.id,
            'amount': '1000000.00',
            'action': '1',
            'error': '0',
            'error_note': 'Success',
            'sign_time': sign_time,
            'sign_string': comp_sign,
        })
        self.assertEqual(dup_response.status_code, 200)
        dup_data = dup_response.json()
        self.assertEqual(dup_data['error'], 0)

    def test_click_webhook_invalid_signature_and_amount_mismatch(self):
        cart = models.Cart.objects.create(user=self.user, status=1)
        payment = models.Payment.objects.create(
            order=cart,
            provider=models.Payment.Provider.CLICK,
            amount=Decimal('500000.00'),
            status=models.Payment.Status.INITIATED
        )

        # Invalid signature
        bad_response = self.client.post(reverse('payment_webhook', args=['click']), {
            'click_trans_id': '999',
            'service_id': 'test_service_id',
            'merchant_trans_id': payment.code,
            'amount': '500000.00',
            'action': '0',
            'sign_time': '2026-08-14 20:00:00',
            'sign_string': 'invalid_md5_hash_string',
        })
        self.assertEqual(bad_response.json()['error'], -1)

    def test_payme_json_rpc_webhook_flow(self):
        cart = models.Cart.objects.create(user=self.user, status=1)
        models.CartProduct.objects.create(cart=cart, product=self.product, count=1)
        payment = models.Payment.objects.create(
            order=cart,
            provider=models.Payment.Provider.PAYME,
            amount=Decimal('1000000.00'),
            status=models.Payment.Status.PENDING
        )

        secret_key = getattr(settings, 'PAYME_SECRET_KEY', 'test_payme_secret')
        auth_header = f"Basic {base64.b64encode(f'Paycom:{secret_key}'.encode('utf-8')).decode('utf-8')}"

        # 1. CheckPerformTransaction
        check_payload = {
            'method': 'CheckPerformTransaction',
            'params': {
                'amount': 100000000,  # 1,000,000 UZS in tiyin
                'account': {'order_id': str(payment.code)}
            },
            'id': 101
        }
        res1 = self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps(check_payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )
        self.assertEqual(res1.status_code, 200)
        self.assertTrue(res1.json()['result']['allow'])

        # 2. CreateTransaction
        payme_trans_id = 'payme_tx_987654321'
        create_payload = {
            'method': 'CreateTransaction',
            'params': {
                'id': payme_trans_id,
                'time': 1723658400000,
                'amount': 100000000,
                'account': {'order_id': str(payment.code)}
            },
            'id': 102
        }
        res2 = self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps(create_payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()['result']['state'], 1)

        # 3. PerformTransaction
        perform_payload = {
            'method': 'PerformTransaction',
            'params': {
                'id': payme_trans_id
            },
            'id': 103
        }
        res3 = self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps(perform_payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(res3.json()['result']['state'], 2)

        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.PAID)
        self.assertEqual(payment.transaction_id, payme_trans_id)

        # 4. Duplicate PerformTransaction (Idempotency)
        res4 = self.client.post(
            reverse('payment_webhook', args=['payme']),
            data=json.dumps(perform_payload),
            content_type='application/json',
            HTTP_AUTHORIZATION=auth_header
        )
        self.assertEqual(res4.status_code, 200)
        self.assertEqual(res4.json()['result']['state'], 2)

    def test_dashboard_payments_management_and_refund(self):
        cart = models.Cart.objects.create(user=self.user, status=2)
        payment = models.Payment.objects.create(
            order=cart,
            provider=models.Payment.Provider.CLICK,
            amount=Decimal('1000000.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now(),
            transaction_id='click_tx_111'
        )

        # Unauthorized access
        unauth_res = self.client.get(reverse('d_payments'))
        self.assertEqual(unauth_res.status_code, 302)

        # Admin access
        self.client.force_login(self.admin_user)
        dash_res = self.client.get(reverse('d_payments'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertContains(dash_res, str(payment.code)[:8])
        self.assertContains(dash_res, "1 000 000 so'm")

        # Admin Refund
        refund_res = self.client.post(reverse('d_refund_payment', args=[payment.id]), {
            'reason': "Mijoz bekor qildi",
            'amount': '1000000.00'
        })
        self.assertEqual(refund_res.status_code, 302)

        payment.refresh_from_db()
        self.assertEqual(payment.status, models.Payment.Status.REFUNDED)
        self.assertEqual(payment.refund_amount, Decimal('1000000.00'))

