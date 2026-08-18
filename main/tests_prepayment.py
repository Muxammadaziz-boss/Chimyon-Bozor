import json
from decimal import Decimal
from django.test import TestCase, Client, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse

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

        # Category and Product with price 967,600 UZS
        self.category = models.Category.objects.create(name='Elektronika')
        self.product = models.Product.objects.create(
            name='Smartfon X Pro',
            category=self.category,
            price=967600.00,
            discount_price=None,
            discount_status=False,
            count=10
        )

        # Site Settings with allowed options 30, 50, 100
        self.settings = models.SiteSettings.objects.create(
            site_name="Chimyon-bozor",
            prepayment_enabled=True,
            prepayment_percent=30,
            allowed_prepayment_percentages="30,50,100",
            allow_cash_balance=True,
            allow_online_balance_payment=True
        )

        # Cart for customer with 1 item = 967,600 UZS
        self.cart = models.Cart.objects.create(user=self.customer, status=1)
        self.cart_product = models.CartProduct.objects.create(
            cart=self.cart,
            product=self.product,
            count=1
        )

    # -------------------------------------------------------------
    # 1. 30% Calculation Test
    # -------------------------------------------------------------
    def test_30_percent_calculation(self):
        financials = PaymentManager.calculate_order_financials(self.cart, chosen_percent=30)
        self.assertEqual(financials['grand_total'], Decimal('967600.00'))
        self.assertEqual(financials['prepayment_percent'], 30)
        self.assertEqual(financials['prepayment_amount'], Decimal('290280.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('677320.00'))
        self.assertEqual(financials['remaining_on_delivery'], Decimal('677320.00'))

    # -------------------------------------------------------------
    # 2. 50% Calculation Test
    # -------------------------------------------------------------
    def test_50_percent_calculation(self):
        financials = PaymentManager.calculate_order_financials(self.cart, chosen_percent=50)
        self.assertEqual(financials['grand_total'], Decimal('967600.00'))
        self.assertEqual(financials['prepayment_percent'], 50)
        self.assertEqual(financials['prepayment_amount'], Decimal('483800.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('483800.00'))
        self.assertEqual(financials['remaining_on_delivery'], Decimal('483800.00'))

    # -------------------------------------------------------------
    # 3. 100% Calculation Test
    # -------------------------------------------------------------
    def test_100_percent_calculation(self):
        financials = PaymentManager.calculate_order_financials(self.cart, chosen_percent=100)
        self.assertEqual(financials['grand_total'], Decimal('967600.00'))
        self.assertEqual(financials['prepayment_percent'], 100)
        self.assertEqual(financials['prepayment_amount'], Decimal('967600.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('0.00'))
        self.assertEqual(financials['remaining_on_delivery'], Decimal('0.00'))

    # -------------------------------------------------------------
    # 4. Remaining Amount Calculation Test (Never equals grand_total if prepayment > 0)
    # -------------------------------------------------------------
    def test_remaining_amount_calculation_never_equals_grand_total_when_prepayment_exists(self):
        financials = PaymentManager.calculate_order_financials(self.cart, chosen_percent=30)
        self.assertNotEqual(financials['remaining_amount'], financials['grand_total'])
        self.assertEqual(financials['remaining_amount'], Decimal('677320.00'))
        self.assertEqual(financials['grand_total'] - financials['prepayment_amount'], financials['remaining_amount'])

    # -------------------------------------------------------------
    # 5. Prepayment Payment Creation Test
    # -------------------------------------------------------------
    def test_prepayment_payment_creation(self):
        payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            chosen_percent=30
        )
        self.assertEqual(payment.amount, Decimal('290280.00'))
        self.assertEqual(payment.purpose, models.Payment.Purpose.PREPAYMENT)
        self.assertEqual(payment.provider, 'click')
        self.assertIn('amount=290280.00', checkout_url)
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.prepayment_percent, 30)
        self.assertEqual(self.cart.prepayment_amount, Decimal('290280.00'))

    # -------------------------------------------------------------
    # 6. Successful Prepayment Transition Test
    # -------------------------------------------------------------
    def test_successful_prepayment(self):
        payment, _ = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            chosen_percent=30
        )
        payment.status = models.Payment.Status.PAID
        payment.paid_at = timezone.now()
        payment.save()

        self.cart.status = 2  # Accepted
        PaymentManager.sync_order_financial_status(self.cart)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('290280.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('677320.00'))
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)
        self.assertTrue(self.cart.is_partially_paid)
        self.assertFalse(self.cart.is_fully_paid)

    # -------------------------------------------------------------
    # 7. Balance Payment Test
    # -------------------------------------------------------------
    def test_balance_payment(self):
        # Step 1: Prepayment 30%
        payment, _ = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            chosen_percent=30
        )
        payment.status = models.Payment.Status.PAID
        payment.paid_at = timezone.now()
        payment.save()
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        # Step 2: Pay Remaining Balance (677,320 UZS)
        bal_payment, bal_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            purpose=models.Payment.Purpose.BALANCE
        )
        self.assertEqual(bal_payment.amount, Decimal('677320.00'))
        self.assertEqual(bal_payment.purpose, models.Payment.Purpose.BALANCE)
        self.assertIn('amount=677320.00', bal_url)

        bal_payment.status = models.Payment.Status.PAID
        bal_payment.paid_at = timezone.now()
        bal_payment.save()
        PaymentManager.sync_order_financial_status(self.cart)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('967600.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('0.00'))
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.FULLY_PAID)
        self.assertTrue(self.cart.is_fully_paid)

    # -------------------------------------------------------------
    # 8. Full Payment Test (100% Prepayment)
    # -------------------------------------------------------------
    def test_full_payment(self):
        payment, _ = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            chosen_percent=100
        )
        self.assertEqual(payment.amount, Decimal('967600.00'))
        self.assertEqual(payment.purpose, models.Payment.Purpose.FULL)

        payment.status = models.Payment.Status.PAID
        payment.paid_at = timezone.now()
        payment.save()
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('967600.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('0.00'))
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.FULLY_PAID)

    # -------------------------------------------------------------
    # 9. Invalid Percentage Test
    # -------------------------------------------------------------
    def test_invalid_percentage(self):
        with self.assertRaises(ValueError):
            PaymentManager.calculate_order_financials(self.cart, chosen_percent=-10)

        with self.assertRaises(ValueError):
            PaymentManager.calculate_order_financials(self.cart, chosen_percent=150)

    # -------------------------------------------------------------
    # 10. Unauthorized Percentage Test (e.g. 73%)
    # -------------------------------------------------------------
    def test_unauthorized_percentage(self):
        with self.assertRaises(ValueError):
            PaymentManager.calculate_order_financials(self.cart, chosen_percent=73)

        with self.assertRaises(ValueError):
            PaymentManager.create_payment(
                order=self.cart,
                provider_name='click',
                chosen_percent=73
            )

    # -------------------------------------------------------------
    # 11. Tampered Client Amount Ignored Test
    # -------------------------------------------------------------
    def test_tampered_client_amount(self):
        # Even if someone calls payment creation, only server calculated prepayment_amount is accepted
        payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            chosen_percent=30
        )
        # Authoritative server amount is 290,280.00 UZS
        self.assertEqual(payment.amount, Decimal('290280.00'))
        self.assertIn('amount=290280.00', checkout_url)
        self.assertNotIn('amount=1.00', checkout_url)

    # -------------------------------------------------------------
    # 12. Delivery Fee and Discount Included Test
    # -------------------------------------------------------------
    def test_delivery_fee_and_discount_calculation(self):
        # Product with discount
        disc_product = models.Product.objects.create(
            name='Chegirmali Tovar',
            category=self.category,
            price=1000000.00,
            discount_price=900000.00,
            discount_status=True,
            count=5
        )
        cart2 = models.Cart.objects.create(user=self.customer, status=1)
        models.CartProduct.objects.create(cart=cart2, product=disc_product, count=1)

        financials = PaymentManager.calculate_order_financials(cart2, chosen_percent=30)
        self.assertEqual(financials['grand_total'], Decimal('900000.00'))
        self.assertEqual(financials['prepayment_amount'], Decimal('270000.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('630000.00'))

    # -------------------------------------------------------------
    # 13. Zero Balance Settlement Guard Test
    # -------------------------------------------------------------
    def test_zero_balance_settlement_guard(self):
        # Set order as fully paid
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.FULL,
            amount=Decimal('967600.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        with self.assertRaises(ValueError):
            PaymentManager.create_payment(
                order=self.cart,
                provider_name='click',
                purpose=models.Payment.Purpose.BALANCE
            )

        with self.assertRaises(ValueError):
            PaymentManager.settle_cash_balance(order=self.cart)

    # -------------------------------------------------------------
    # 14. Overpayment Protection Test
    # -------------------------------------------------------------
    def test_overpayment_protection(self):
        # Prepayment 30%
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('290280.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )
        self.cart.status = 2
        PaymentManager.sync_order_financial_status(self.cart)

        # Remaining is 677,320 UZS. Attempting to settle 800,000 UZS raises ValueError
        with self.assertRaises(ValueError):
            PaymentManager.settle_cash_balance(order=self.cart, amount=Decimal('800000.00'))

    # -------------------------------------------------------------
    # 15. Checkout UI Flow & Options Breakdown Test
    # -------------------------------------------------------------
    def test_checkout_view_renders_prepayment_options_and_correct_balance(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('checkout'))
        self.assertEqual(response.status_code, 200)

        financials = response.context['financials']
        self.assertEqual(financials['grand_total'], Decimal('967600.00'))
        self.assertEqual(financials['prepayment_amount'], Decimal('290280.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('677320.00'))

        # Check options breakdown
        self.assertEqual(len(financials['percentage_options']), 3)
        self.assertEqual(financials['options_breakdown'][30]['prepayment_amount'], Decimal('290280.00'))
        self.assertEqual(financials['options_breakdown'][30]['remaining_amount'], Decimal('677320.00'))
        self.assertEqual(financials['options_breakdown'][50]['prepayment_amount'], Decimal('483800.00'))
        self.assertEqual(financials['options_breakdown'][50]['remaining_amount'], Decimal('483800.00'))
        self.assertEqual(financials['options_breakdown'][100]['prepayment_amount'], Decimal('967600.00'))
        self.assertEqual(financials['options_breakdown'][100]['remaining_amount'], Decimal('0.00'))

        # Check HTML content
        content = response.content.decode('utf-8')
        self.assertIn('967 600', content)
        self.assertIn('290 280', content)
        self.assertIn('677 320', content)

    # -------------------------------------------------------------
    # 16. Checkout Post with Custom Percentage (e.g. 50%)
    # -------------------------------------------------------------
    def test_checkout_post_with_selected_50_percent(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('checkout'), {
            'phone': '+998901234567',
            'address': 'Yunusobod',
            'provider': 'click',
            'prepayment_percent': '50'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('https://my.click.uz/services/pay?'))
        self.assertIn('amount=483800.00', response.url)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.prepayment_percent, 50)
        self.assertEqual(self.cart.prepayment_amount, Decimal('483800.00'))

    # -------------------------------------------------------------
    # 17. Checkout Post with Unauthorized Percentage (e.g. 73%) Rejection (PRG)
    # -------------------------------------------------------------
    def test_checkout_post_unauthorized_percentage_rejected(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('checkout'), {
            'phone': '+998901234567',
            'address': 'Yunusobod',
            'provider': 'click',
            'prepayment_percent': '73'
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context['messages'])
        self.assertTrue(any("Ruxsat etilmagan" in m.message for m in messages_list))

    # -------------------------------------------------------------
    # 18. Phone Validation Test (Rejects letters like '+998917914881das' via PRG)
    # -------------------------------------------------------------
    def test_checkout_post_invalid_phone_with_letters_rejected(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('checkout'), {
            'phone': '+998917914881das',
            'address': 'Yunusobod',
            'provider': 'click',
            'prepayment_percent': '30'
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context['messages'])
        self.assertTrue(any("Telefon raqami noto'g'ri kiritildi" in m.message for m in messages_list))

    # -------------------------------------------------------------
    # 19. Admin Address List Selection & Validation Test (PRG)
    # -------------------------------------------------------------
    def test_checkout_address_selection_from_admin_list(self):
        models.Address.objects.create(name="Chimyon", is_active=True)
        models.Address.objects.create(name="Farg'ona", is_active=True)

        self.client.force_login(self.customer)
        
        # Valid address selection
        response = self.client.post(reverse('checkout'), {
            'phone': '+998901234567',
            'address': "Farg'ona",
            'provider': 'click',
            'prepayment_percent': '30'
        })
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.address, "Farg'ona")

        # Invalid address not in admin list (PRG Redirects back with error)
        self.cart.status = 1
        self.cart.save()
        bad_response = self.client.post(reverse('checkout'), {
            'phone': '+998901234567',
            'address': "Noma'lum qishloq 123",
            'provider': 'click',
            'prepayment_percent': '30'
        }, follow=True)
        self.assertEqual(bad_response.status_code, 200)
        messages_list = list(bad_response.context['messages'])
        self.assertTrue(any("admin tomonidan qo'shilgan" in m.message for m in messages_list))

    # -------------------------------------------------------------
    # 20. Idempotent Payment Retry Test (No duplicate payment creation)
    # -------------------------------------------------------------
    def test_checkout_idempotency_retry_does_not_duplicate_payments(self):
        self.client.force_login(self.customer)
        
        # First submission
        res1 = self.client.post(reverse('checkout'), {
            'phone': '+998901234567',
            'address': 'Chimyon',
            'provider': 'click',
            'prepayment_percent': '30'
        })
        self.assertEqual(res1.status_code, 302)
        self.assertEqual(models.Payment.objects.filter(order=self.cart).count(), 1)

        # Retry / back / re-post with same params
        res2 = self.client.post(reverse('checkout'), {
            'phone': '+998901234567',
            'address': 'Chimyon',
            'provider': 'click',
            'prepayment_percent': '30'
        })
        self.assertEqual(res2.status_code, 302)
        # Should still be 1 active payment, no duplicate ghost rows!
        self.assertEqual(models.Payment.objects.filter(order=self.cart, status__in=[models.Payment.Status.INITIATED, models.Payment.Status.PENDING]).count(), 1)

    # -------------------------------------------------------------
    # 21. Pay Balance View with Decimal Integrity & Click Checkout URL
    # -------------------------------------------------------------
    def test_pay_balance_view_creates_balance_payment_with_decimal_integrity(self):
        # Setup order: 30% prepayment settled
        self.cart.status = 2  # Accepted / in progress
        self.cart.prepayment_percent = 30
        self.cart.prepayment_amount = Decimal('290280.00')
        self.cart.financial_status = models.Cart.FinancialStatus.PARTIALLY_PAID
        self.cart.save()

        # Create paid prepayment
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('290280.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )

        self.assertEqual(self.cart.paid_amount, Decimal('290280.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('677320.00'))

        self.client.force_login(self.customer)
        response = self.client.post(reverse('pay_balance', kwargs={'code': self.cart.code}), {
            'provider': 'click'
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('https://my.click.uz/services/pay?'))
        self.assertIn('amount=677320.00', response.url)

        # Check balance payment record
        balance_payment = models.Payment.objects.filter(order=self.cart, purpose=models.Payment.Purpose.BALANCE).first()
        self.assertIsNotNone(balance_payment)
        self.assertEqual(balance_payment.amount, Decimal('677320.00'))
        self.assertEqual(balance_payment.provider, 'click')
        self.assertEqual(balance_payment.status, models.Payment.Status.INITIATED)

    # -------------------------------------------------------------
    # 22. Pay Balance Rejects When Order Already Fully Paid (Zero Balance)
    # -------------------------------------------------------------
    def test_pay_balance_rejects_when_order_already_fully_paid(self):
        self.cart.status = 2
        self.cart.prepayment_percent = 100
        self.cart.prepayment_amount = Decimal('967600.00')
        self.cart.financial_status = models.Cart.FinancialStatus.FULLY_PAID
        self.cart.save()

        # Full payment record
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.FULL,
            amount=Decimal('967600.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )

        self.assertEqual(self.cart.remaining_amount, Decimal('0.00'))

        self.client.force_login(self.customer)
        response = self.client.post(reverse('pay_balance', kwargs={'code': self.cart.code}), {
            'provider': 'click'
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context['messages'])
        self.assertTrue(any("allaqachon to'liq to'langan" in m.message for m in messages_list))
        # No balance payment created
        self.assertEqual(models.Payment.objects.filter(order=self.cart, purpose=models.Payment.Purpose.BALANCE).count(), 0)

    # -------------------------------------------------------------
    # 23. Complete 967,600 / 30% Lifecycle Scenario Test
    # -------------------------------------------------------------
    def test_prepayment_and_balance_full_lifecycle(self):
        # Scenario: 967,600 UZS Grand Total, 30% Prepayment (290,280 UZS), Remaining (677,320 UZS)
        self.cart.status = 2
        self.cart.prepayment_percent = 30
        self.cart.prepayment_amount = Decimal('290280.00')
        self.cart.financial_status = models.Cart.FinancialStatus.UNPAID
        self.cart.save()

        # Step 1: Prepayment is INITIATED
        prep_payment = models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('290280.00'),
            status=models.Payment.Status.INITIATED
        )
        self.assertEqual(self.cart.paid_amount, Decimal('0.00'))
        financials = PaymentManager.calculate_order_financials(self.cart)
        self.assertEqual(financials['prepayment_amount'], Decimal('290280.00'))
        self.assertEqual(financials['remaining_amount'], Decimal('677320.00'))

        # Step 2: Prepayment webhook confirms PAID
        prep_payment.status = models.Payment.Status.PAID
        prep_payment.paid_at = timezone.now()
        prep_payment.save()
        PaymentManager.sync_order_financial_status(self.cart)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)
        self.assertEqual(self.cart.paid_amount, Decimal('290280.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('677320.00'))

        # Step 3: Customer pays remaining balance via online Click
        bal_payment, checkout_url = PaymentManager.create_payment(
            order=self.cart,
            provider_name='click',
            purpose=models.Payment.Purpose.BALANCE
        )
        self.assertEqual(bal_payment.amount, Decimal('677320.00'))
        self.assertIn('amount=677320.00', checkout_url)

        # Step 4: Balance payment confirmed PAID
        bal_payment.status = models.Payment.Status.PAID
        bal_payment.paid_at = timezone.now()
        bal_payment.save()
        PaymentManager.sync_order_financial_status(self.cart)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.FULLY_PAID)
        self.assertEqual(self.cart.paid_amount, Decimal('967600.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('0.00'))
        self.assertTrue(self.cart.is_fully_paid)

    # -------------------------------------------------------------
    # 24. Pay Balance Unauthorized User Rejection (404)
    # -------------------------------------------------------------
    def test_pay_balance_unauthorized_user_rejected(self):
        other_user = models.User.objects.create_user(username='intruder', phone='+998909998877', password='password123')
        self.cart.status = 2
        self.cart.save()

        self.client.force_login(other_user)
        response = self.client.post(reverse('pay_balance', kwargs={'code': self.cart.code}), {
            'provider': 'click'
        })
        self.assertEqual(response.status_code, 404)

    # -------------------------------------------------------------
    # 25. Pay Balance Invalid Provider Validation
    # -------------------------------------------------------------
    def test_pay_balance_invalid_provider_rejected(self):
        self.cart.status = 2
        self.cart.save()

        self.client.force_login(self.customer)
        response = self.client.post(reverse('pay_balance', kwargs={'code': self.cart.code}), {
            'provider': 'bitcoin_invalid'
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context['messages'])
        self.assertTrue(any("Noto'g'ri to'lov provayderi" in m.message for m in messages_list))

    # -------------------------------------------------------------
    # 26. Pay Balance Idempotency Test (No duplicate balance payments)
    # -------------------------------------------------------------
    def test_pay_balance_idempotency_retry_does_not_duplicate_rows(self):
        self.cart.status = 2
        self.cart.save()

        self.client.force_login(self.customer)
        
        # 1st attempt
        res1 = self.client.post(reverse('pay_balance', kwargs={'code': self.cart.code}), {
            'provider': 'click'
        })
        self.assertEqual(res1.status_code, 302)
        self.assertEqual(models.Payment.objects.filter(order=self.cart, purpose=models.Payment.Purpose.BALANCE).count(), 1)

        # 2nd attempt (same provider/amount)
        res2 = self.client.post(reverse('pay_balance', kwargs={'code': self.cart.code}), {
            'provider': 'click'
        })
        self.assertEqual(res2.status_code, 302)
        # Should still be 1 active balance payment, not duplicated!
        self.assertEqual(models.Payment.objects.filter(order=self.cart, purpose=models.Payment.Purpose.BALANCE, status__in=[models.Payment.Status.INITIATED, models.Payment.Status.PENDING]).count(), 1)

    # -------------------------------------------------------------
    # 27. Delivery-Time Cash Balance Payment as Real Payment Record
    # -------------------------------------------------------------
    def test_delivery_cash_balance_creates_real_payment_record_and_ledger(self):
        staff_user = models.User.objects.create_user(username='courier_admin', phone='+998901112233', password='adminpass123', is_staff=True)
        self.cart.status = 3  # In delivery
        self.cart.prepayment_percent = 30
        self.cart.prepayment_amount = Decimal('290280.00')
        self.cart.financial_status = models.Cart.FinancialStatus.PARTIALLY_PAID
        self.cart.save()

        # 1. Prepayment Record (Paid)
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('290280.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )

        self.assertEqual(self.cart.paid_amount, Decimal('290280.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('677320.00'))

        # 2. Staff collects cash balance at delivery time
        self.client.force_login(staff_user)
        response = self.client.post(reverse('d_settle_order_balance', kwargs={'code': self.cart.code}), {
            'amount': '677320.00',
            'provider': 'cash',
            'comment': 'Mijozdan yetkazish vaqtida to\'liq qabul qilindi'
        })
        self.assertEqual(response.status_code, 302)

        # 3. Verify Payment Ledger contains 2 real records
        payments = models.Payment.objects.filter(order=self.cart).order_by('created_at')
        self.assertEqual(payments.count(), 2)

        prep_p = payments[0]
        self.assertEqual(prep_p.purpose, models.Payment.Purpose.PREPAYMENT)
        self.assertEqual(prep_p.provider, 'click')
        self.assertEqual(prep_p.amount, Decimal('290280.00'))
        self.assertEqual(prep_p.status, models.Payment.Status.PAID)

        bal_p = payments[1]
        self.assertEqual(bal_p.purpose, models.Payment.Purpose.BALANCE)
        self.assertEqual(bal_p.provider, 'cash')
        self.assertEqual(bal_p.amount, Decimal('677320.00'))
        self.assertEqual(bal_p.status, models.Payment.Status.PAID)

        # 4. Verify Financial Totals
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.paid_amount, Decimal('967600.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('0.00'))
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.FULLY_PAID)
        self.assertTrue(self.cart.is_fully_paid)

    # -------------------------------------------------------------
    # 28. Delivery Status Does Not Automatically Mark Payment as Paid
    # -------------------------------------------------------------
    def test_delivery_status_does_not_auto_mark_payment_paid(self):
        staff_user = models.User.objects.create_user(username='courier2', phone='+998902223344', password='adminpass123', is_staff=True)
        self.cart.status = 3  # In delivery
        self.cart.prepayment_percent = 30
        self.cart.prepayment_amount = Decimal('290280.00')
        self.cart.financial_status = models.Cart.FinancialStatus.PARTIALLY_PAID
        self.cart.save()

        # Prepayment paid
        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('290280.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )

        # Admin marks order as Delivered (status=4)
        self.client.force_login(staff_user)
        res = self.client.post(reverse('d_update_status', kwargs={'code': self.cart.code}), {
            'target_status': '4',
            'comment': 'Yetkazildi lekin qoldiq hali olinmadi'
        })
        self.assertEqual(res.status_code, 302)

        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, 4)  # Delivered
        # Financial status MUST still be PARTIALLY_PAID because balance wasn't paid yet!
        self.assertEqual(self.cart.financial_status, models.Cart.FinancialStatus.PARTIALLY_PAID)
        self.assertEqual(self.cart.paid_amount, Decimal('290280.00'))
        self.assertEqual(self.cart.remaining_amount, Decimal('677320.00'))

    # -------------------------------------------------------------
    # 29. Customer Cannot Settle Cash Balance Via Dashboard Endpoint
    # -------------------------------------------------------------
    def test_customer_cannot_settle_cash_balance(self):
        self.cart.status = 3
        self.cart.save()

        self.client.force_login(self.customer)  # Non-staff customer
        response = self.client.post(reverse('d_settle_order_balance', kwargs={'code': self.cart.code}), {
            'amount': '677320.00',
            'provider': 'cash'
        })
        # Should redirect to login or deny permission
        self.assertEqual(response.status_code, 302)
        self.assertTrue('login' in response.url or 'd_index' in response.url)

    # -------------------------------------------------------------
    # 30. Delivery Cash Balance Overpayment Rejected
    # -------------------------------------------------------------
    def test_cash_balance_overpayment_rejected(self):
        staff_user = models.User.objects.create_user(username='staff_overpay', phone='+998903334455', password='adminpass123', is_staff=True)
        self.cart.status = 3
        self.cart.prepayment_percent = 30
        self.cart.prepayment_amount = Decimal('290280.00')
        self.cart.financial_status = models.Cart.FinancialStatus.PARTIALLY_PAID
        self.cart.save()

        models.Payment.objects.create(
            order=self.cart,
            provider='click',
            purpose=models.Payment.Purpose.PREPAYMENT,
            amount=Decimal('290280.00'),
            status=models.Payment.Status.PAID,
            paid_at=timezone.now()
        )

        self.client.force_login(staff_user)
        # Remaining is 677,320, admin inputs 700,000
        response = self.client.post(reverse('d_settle_order_balance', kwargs={'code': self.cart.code}), {
            'amount': '700000.00',
            'provider': 'cash'
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context['messages'])
        self.assertTrue(any("ortiq bo'lishi mumkin emas" in m.message for m in messages_list))
        # No extra payment created
        self.assertEqual(models.Payment.objects.filter(order=self.cart, purpose=models.Payment.Purpose.BALANCE).count(), 0)

    # -------------------------------------------------------------
    # 31. Export Payments Excel Endpoint Test
    # -------------------------------------------------------------
    def test_export_payments_excel_view(self):
        staff_user = models.User.objects.create_user(username='staff_export', phone='+998904445566', password='adminpass123', is_staff=True)
        self.client.force_login(staff_user)

        response = self.client.get(reverse('d_export_payments'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        self.assertIn('chimyon_bozor_tolovlar_', response['Content-Disposition'])

