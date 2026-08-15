import json
from decimal import Decimal
from django.test import TestCase, Client, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse

from main import models
from main.services.payment import PaymentManager

User = get_user_model()


@override_settings(
    CLICK_SERVICE_ID='54321',
    CLICK_MERCHANT_ID='12345',
    CLICK_SECRET_KEY='click_sec_123',
    PAYME_MERCHANT_ID='payme_123',
    PAYME_SECRET_KEY='payme_sec_123',
    UZUM_MERCHANT_ID='uzum_123',
    UZUM_SECRET_KEY='uzum_sec_123'
)
class PartialPrepaymentAndBalanceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()

        # Users
        self.customer = User.objects.create_user(
            username='testcustomer',
            password='password123',
            phone='+998901234567',
            address='Toshkent sh., Yunusobod tumani'
        )
        self.other_user = User.objects.create_user(
            username='othercustomer',
            password='password123',
            phone='+998907654321',
            address='Samarqand sh.'
        )
        self.admin_user = User.objects.create_superuser(
            username='adminuser',
            password='password123',
            email='admin@chimyon-bozor.uz'
        )

        # Category and Product
        self.category = models.Category.objects.create(name='Elektronika')
        self.product = models.Product.objects.create(
            name='Smartfon X Pro',
            category=self.category,
            price=100000.00,
            discount_price=None,
            discount_status=False,
            count=10
        )

        # Site Settings
        self.settings = models.SiteSettings.objects.create(
            site_name="Chimyon-bozor",
            prepayment_enabled=True,
            prepayment_percent=30,
            allow_cash_balance=True,
            allow_online_balance_payment=True
        )

        # Cart for customer with 2 items = 200,000 UZS
        self.cart = models.Cart.objects.create(user=self.customer, status=1)
        self.cart_product = models.CartProduct.objects.create(
            cart=self.cart,
            product=self.product,
            count=2
        )

    # -------------------------------------------------------------
    # 1. Calculation Tests (30%, 50%, 100%, 0%)
    # -------------------------------------------------------------
    def test_prepayment_calculation_30_percent(self):
        self.settings.prepayment_percent = 30
        self.settings.save()

        financials = PaymentManager.calculate_order_financials(self.cart)
        self.assertEqual(financials['grand_total'], Decimal('200000.00'))
        self.assertEqual(financials['prepayment_percent'], 30)
        self.assertEqual(financials['prepayment_amount'], Decimal('60000.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('200000.00'))
        self.assertEqual(financials['financial_status'], models.Cart.FinancialStatus.UNPAID)

    def test_prepayment_calculation_50_percent(self):
        self.settings.prepayment_percent = 50
        self.settings.save()

        financials = PaymentManager.calculate_order_financials(self.cart)
        self.assertEqual(financials['grand_total'], Decimal('200000.00'))
        self.assertEqual(financials['prepayment_percent'], 50)
        self.assertEqual(financials['prepayment_amount'], Decimal('100000.00'))

    def test_prepayment_calculation_100_percent(self):
        self.settings.prepayment_percent = 100
        self.settings.save()

        financials = PaymentManager.calculate_order_financials(self.cart)
        self.assertEqual(financials['grand_total'], Decimal('200000.00'))
        self.assertEqual(financials['prepayment_percent'], 100)
        self.assertEqual(financials['prepayment_amount'], Decimal('200000.00'))

    def test_prepayment_calculation_0_percent(self):
        self.settings.prepayment_enabled = False
        self.settings.save()

        financials = PaymentManager.calculate_order_financials(self.cart)
        self.assertEqual(financials['prepayment_percent'], 0)
        self.assertEqual(financials['prepayment_amount'], Decimal('0.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('200000.00'))

    # -------------------------------------------------------------
    # 2. Payment Creation & Initial Policy Validation
    # -------------------------------------------------------------
    def test_prepayment_cash_rejected_when_prepayment_required(self):
        self.settings.prepayment_percent = 30
        self.settings.save()

        with self.assertRaises(ValueError):
            PaymentManager.create_payment(
                order=self.cart,
                provider_name='cash'
            )

    def test_create_payment_prepayment_success(self):
        self.settings.prepayment_percent = 30
        self.settings.save()

        payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click'
        )

        self.cart.refresh_from_db()
        self.assertEqual(payment.amount, Decimal('60000.00'))
        self.assertEqual(payment.purpose, models.Payment.Purpose.PREPAYMENT)
        self.assertEqual(payment.status, models.Payment.Status.INITIATED)
        self.assertEqual(self.cart.prepayment_percent, 30)
        self.assertEqual(self.cart.prepayment_amount, Decimal('60000.00'))
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.UNPAID)
        self.assertIn('click', checkout_url.lower())

    def test_0_percent_cash_checkout_accepts_order_and_deducts_stock(self):
        self.settings.prepayment_enabled = False
        self.settings.save()

        payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='cash'
        )

        self.cart.refresh_from_db()
        self.product.refresh_from_db()

        self.assertEqual(self.cart.status, 2)  # Accepted
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.UNPAID)
        self.assertEqual(self.product.count, 8)  # 10 - 2 = 8
        self.assertEqual(payment.amount, Decimal('200000.00'))
        self.assertEqual(payment.purpose, models.Payment.Purpose.FULL)

    # -------------------------------------------------------------
    # 3. Webhook Handling & Stock Integrity (Click, Payme, Uzum)
    # -------------------------------------------------------------
    def test_webhook_click_success_prepayment_and_single_stock_deduction(self):
        payment, _ = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click'
        )

        from main.services.payment.click import ClickPaymentProvider
        provider = ClickPaymentProvider()

        import hashlib
        sign_time = '2026-08-15 12:00:00'
        sign_str = hashlib.md5(
            f"11223344{provider.get_service_id()}{provider.get_secret_key()}{payment.code}1{payment.amount}{ClickPaymentProvider.ACTION_COMPLETE}{sign_time}".encode('utf-8')
        ).hexdigest()

        # Simulate Click COMPLETE request
        click_data = {
            'click_trans_id': '11223344',
            'service_id': provider.get_service_id(),
            'merchant_trans_id': payment.code,
            'merchant_prepare_id': '1',
            'amount': str(payment.amount),
            'action': ClickPaymentProvider.ACTION_COMPLETE,
            'error': ClickPaymentProvider.ERROR_SUCCESS,
            'error_note': 'Success',
            'sign_time': sign_time,
            'sign_string': sign_str
        }

        req = self.factory.post('/payments/webhook/click/', data=click_data)
        response = provider.handle_webhook(req)

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.cart.refresh_from_db()
        self.product.refresh_from_db()

        self.assertEqual(payment.status, models.Payment.Status.PAID)
        self.assertEqual(self.cart.status, 2)  # Moved to Accepted
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)
        self.assertEqual(self.cart.paid_amount, Decimal('60000.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('140000.00'))
        self.assertFalse(self.cart.is_fully_paid)
        self.assertTrue(self.cart.is_partially_paid)
        self.assertEqual(self.product.count, 8)  # Decremented by 2

    def test_webhook_idempotency_duplicate_webhook_does_not_deduct_stock_again(self):
        payment, _ = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click'
        )

        from main.services.payment.click import ClickPaymentProvider
        provider = ClickPaymentProvider()

        import hashlib
        sign_time = '2026-08-15 12:00:00'
        sign_str = hashlib.md5(
            f"99887766{provider.get_service_id()}{provider.get_secret_key()}{payment.code}1{payment.amount}{ClickPaymentProvider.ACTION_COMPLETE}{sign_time}".encode('utf-8')
        ).hexdigest()

        click_data = {
            'click_trans_id': '99887766',
            'service_id': provider.get_service_id(),
            'merchant_trans_id': payment.code,
            'merchant_prepare_id': '1',
            'amount': str(payment.amount),
            'action': ClickPaymentProvider.ACTION_COMPLETE,
            'error': ClickPaymentProvider.ERROR_SUCCESS,
            'error_note': 'Success',
            'sign_time': sign_time,
            'sign_string': sign_str
        }

        # First call
        req1 = self.factory.post('/payments/webhook/click/', data=click_data)
        provider.handle_webhook(req1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 8)

        # Duplicate Webhook call
        req2 = self.factory.post('/payments/webhook/click/', data=click_data)
        res2 = provider.handle_webhook(req2)
        self.assertEqual(res2.status_code, 200)

        self.product.refresh_from_db()
        self.assertEqual(self.product.count, 8)  # Still 8, NEVER 6!

    # -------------------------------------------------------------
    # 4. Remaining Balance Settlement (Customer Online & Admin Cash)
    # -------------------------------------------------------------
    def test_customer_pay_balance_online_initiates_payment_with_purpose_balance(self):
        # Step 1: Prepayment 30% completed
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('60000.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        self.assertEqual(self.cart.remaining_amount, Decimal('140000.00'))

        # Step 2: Customer creates balance payment online
        bal_payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='payme',
            purpose=models.Payment.Purpose.BALANCE
        )

        self.assertEqual(bal_payment.purpose, models.Payment.Purpose.BALANCE)
        self.assertEqual(bal_payment.amount, Decimal('140000.00'))
        self.assertEqual(bal_payment.status, models.Payment.Status.INITIATED)
        self.assertTrue('paycom.uz' in checkout_url.lower() or 'checkout' in checkout_url.lower())

    def test_admin_settle_cash_balance_completes_order_to_fully_paid(self):
        # Initial 30% paid
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('60000.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        # Admin marks remaining balance as paid in cash on delivery
        cash_payment = PaymentManager.settle_cash_balance(
            order=self.cart,
            user=self.admin_user,
            comment="Yetkazib berishda kuryer tomonidan naqd qabul qilindi"
        )

        self.cart.refresh_from_db()
        self.assertEqual(cash_payment.status, models.Payment.Status.PAID)
        self.assertEqual(cash_payment.amount, Decimal('140000.00'))
        self.assertEqual(cash_payment.purpose, models.Payment.Purpose.BALANCE)
        self.assertEqual(cash_payment.provider, models.Payment.Provider.CASH)

        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.FULLY_PAID)
        self.assertEqual(self.cart.paid_amount, Decimal('200000.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('0.00'))
        self.assertTrue(self.cart.is_fully_paid)

    def test_admin_settle_partial_balance(self):
        # Initial 30% paid
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('60000.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        # Customer pays 50,000 UZS partial balance
        PaymentManager.settle_cash_balance(
            order=self.cart,
            amount=Decimal('50000.00'),
            user=self.admin_user
        )

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)
        self.assertEqual(self.cart.paid_amount, Decimal('110000.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('90000.00'))

    # -------------------------------------------------------------
    # 5. Overpayment & Negative Amount Protection
    # -------------------------------------------------------------
    def test_overpayment_protection_on_balance_settlement(self):
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('60000.00'),
            status=models.Payment.Status.PAID
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        # Remaining is 140,000. Trying to pay 150,000 should raise ValueError
        with self.assertRaises(ValueError):
            PaymentManager.settle_cash_balance(
                order=self.cart,
                amount=Decimal('150000.00')
            )

    def test_negative_amount_protection_on_balance_settlement(self):
        self.cart.status = 2
        with self.assertRaises(ValueError):
            PaymentManager.settle_cash_balance(
                order=self.cart,
                amount=Decimal('-5000.00')
            )

    def test_cannot_settle_balance_when_order_is_already_fully_paid(self):
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.FULL,
            amount=Decimal('200000.00'),
            status=models.Payment.Status.PAID
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        with self.assertRaises(ValueError):
            PaymentManager.settle_cash_balance(order=self.cart)

    # -------------------------------------------------------------
    # 6. Refund Handling
    # -------------------------------------------------------------
    def test_partial_refund_updates_order_financial_status(self):
        p1 = models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.FULL,
            amount=Decimal('200000.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.FULLY_PAID)

        # Refund 50,000
        res = PaymentManager.refund_payment(p1, amount=50000.00, reason="Mijoz iltimosiga ko'ra")
        self.assertTrue(res['success'])

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('150000.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('50000.00'))
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)

    # -------------------------------------------------------------
    # 7. Stock Replenishment on Order Cancellation
    # -------------------------------------------------------------
    def test_stock_restoration_on_order_rejection(self):
        # Order accepted & stock decremented from 10 to 8
        self.cart.status = 2
        self.cart.save()
        self.product.count = 8
        self.product.save()

        # Admin rejects order via reject_cart view
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('d_reject_cart', kwargs={'code': self.cart.code}), follow=True)

        self.cart.refresh_from_db()
        self.product.refresh_from_db()

        self.assertEqual(self.cart.status, 5)  # Cancelled
        self.assertEqual(self.product.count, 10)  # Restored to 10

    # -------------------------------------------------------------
    # 8. Security & IDOR Protection
    # -------------------------------------------------------------
    def test_idor_protection_customer_cannot_view_others_order(self):
        self.cart.status = 2
        self.cart.save()

        self.client.force_login(self.other_user)
        response = self.client.get(reverse('order_detail', kwargs={'code': self.cart.code}))
        self.assertEqual(response.status_code, 404)

    def test_idor_protection_customer_cannot_pay_others_balance(self):
        self.cart.status = 2
        self.cart.save()

        self.client.force_login(self.other_user)
        response = self.client.post(reverse('pay_balance', kwargs={'code': self.cart.code}), {'provider': 'click'})
        self.assertEqual(response.status_code, 404)

    # -------------------------------------------------------------
    # 9. Dashboard Admin Balance Settlement Action View
    # -------------------------------------------------------------
    def test_admin_dashboard_settle_balance_view(self):
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('60000.00'),
            status=models.Payment.Status.PAID
        )
        self.cart.status = 2
        self.cart.save()
        PaymentManager.sync_order_financial_status(self.cart)

        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse('d_settle_order_balance', kwargs={'code': self.cart.code}),
            {'amount': '140000.00', 'comment': 'Kuryer topshirdi'},
            follow=True
        )

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.FULLY_PAID)
        self.assertEqual(self.cart.remaining_amount, Decimal('0.00'))

    # -------------------------------------------------------------
    # 10. Dashboard Payments List KPI Aggregation
    # -------------------------------------------------------------
    def test_dashboard_payments_list_renders_with_kpis(self):
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('60000.00'),
            status=models.Payment.Status.PAID
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('d_payments'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Oldindan To'lovlar")
        self.assertContains(response, "Kutilayotgan Qoldiqlar")
