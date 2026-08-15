import logging
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple

from django.db import transaction
from django.db.models import F
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from main import models
from .base import BasePaymentProvider, PaymentConfigurationError, PaymentError
from .click import ClickPaymentProvider
from .payme import PaymePaymentProvider
from .uzum import UzumPaymentProvider
from .cash import CashOnDeliveryProvider

logger = logging.getLogger(__name__)


class PaymentManager:
    """
    Markaziy to'lov menejeri (Partial Prepayment + Balance Settlement).
    Barcha to'lov provayderlarini boshqaradi, to'lov yaratadi, webhooklarni yo'naltiradi va refundlarni bajaradi.
    """
    _providers: Dict[str, BasePaymentProvider] = {
        models.Payment.Provider.CLICK: ClickPaymentProvider(),
        models.Payment.Provider.PAYME: PaymePaymentProvider(),
        models.Payment.Provider.UZUM: UzumPaymentProvider(),
        models.Payment.Provider.CASH: CashOnDeliveryProvider(),
    }

    @classmethod
    def get_provider(cls, provider_name: str) -> Optional[BasePaymentProvider]:
        return cls._providers.get(provider_name.lower())

    @classmethod
    def calculate_order_financials(cls, order: models.Cart, chosen_percent: Optional[int] = None) -> Dict[str, Any]:
        """
        Buyurtma uchun server tomonida aniq Decimal hisob-kitob.
        Ruxsat etilgan foizlar (30%, 50%, 100%) va qoldiq summani to'g'ri hisoblaydi.
        """
        grand_total = Decimal(str(order.grand_total)).quantize(Decimal('0.01'))
        paid = Decimal(str(order.paid_amount)).quantize(Decimal('0.01'))
        financial_status = order.financial_status

        settings_obj = models.SiteSettings.get_settings()
        allowed_percentages = settings_obj.get_allowed_percentages()

        if settings_obj and settings_obj.prepayment_enabled:
            # Determine effective prepayment percentage
            if chosen_percent is not None:
                try:
                    chosen_val = int(chosen_percent)
                except (ValueError, TypeError):
                    raise ValueError(f"Noto'g'ri oldindan to'lov foizi: {chosen_percent}")

                if chosen_val in allowed_percentages:
                    prepayment_percent = chosen_val
                else:
                    raise ValueError(f"Ruxsat etilmagan oldindan to'lov foizi: {chosen_val}%. Ruxsat etilganlar: {allowed_percentages}")
            elif order.prepayment_percent and int(order.prepayment_percent) in allowed_percentages:
                prepayment_percent = int(order.prepayment_percent)
            else:
                default_percent = int(settings_obj.prepayment_percent)
                prepayment_percent = default_percent if default_percent in allowed_percentages else allowed_percentages[0]
        else:
            prepayment_percent = 0
            allowed_percentages = [0]

        # Calculate exact prepayment amount
        if prepayment_percent == 0:
            prepayment_amount = Decimal('0.00')
        elif prepayment_percent >= 100:
            prepayment_amount = grand_total
        else:
            prepayment_amount = (grand_total * Decimal(str(prepayment_percent)) / Decimal('100')).quantize(Decimal('0.01'))

        # Calculate remaining balance on delivery
        if paid > Decimal('0.00'):
            # If some payment already settled, remaining is actual unpaid portion
            remaining = max(Decimal('0.00'), grand_total - paid)
        else:
            # Checkout / Pre-payment state: remaining balance to be paid on delivery after paying prepayment
            remaining = max(Decimal('0.00'), grand_total - prepayment_amount)

        # Build authorized breakdowns for all allowed percentage options
        options_breakdown = {}
        percentage_options = []
        for pct in allowed_percentages:
            if pct == 0:
                p_amt = Decimal('0.00')
                r_amt = grand_total
            elif pct >= 100:
                p_amt = grand_total
                r_amt = Decimal('0.00')
            else:
                p_amt = (grand_total * Decimal(str(pct)) / Decimal('100')).quantize(Decimal('0.01'))
                r_amt = max(Decimal('0.00'), grand_total - p_amt)
            
            opt_data = {
                'percent': pct,
                'prepayment_amount': p_amt,
                'remaining_amount': r_amt,
                'is_selected': (pct == prepayment_percent),
            }
            options_breakdown[pct] = opt_data
            percentage_options.append(opt_data)

        return {
            'grand_total': grand_total,
            'paid_amount': paid,
            'remaining_amount': remaining,
            'remaining_on_delivery': remaining,
            'prepayment_percent': prepayment_percent,
            'prepayment_amount': prepayment_amount,
            'allowed_percentages': allowed_percentages,
            'options_breakdown': options_breakdown,
            'percentage_options': percentage_options,
            'financial_status': financial_status,
            'is_fully_paid': order.is_fully_paid or (grand_total > 0 and paid >= grand_total),
            'is_partially_paid': order.is_partially_paid or (paid > 0 and paid < grand_total),
        }

    @classmethod
    def create_payment(
        cls,
        order: models.Cart,
        provider_name: str,
        purpose: Optional[str] = None,
        chosen_percent: Optional[int] = None,
        payment_method: str = "card",
        request: Optional[HttpRequest] = None
    ) -> Tuple[models.Payment, str]:
        """
        Buyurtma uchun yangi to'lov (avans, qoldiq yoki to'liq) yaratadi.
        Faqat server tomonidan hisoblangan Decimal summalarga tayanadi.
        """
        provider_key = provider_name.lower()
        provider = cls.get_provider(provider_key)
        if not provider:
            raise ValueError(f"Noma'lum to'lov provayderi: {provider_name}")

        # Provayder sozlamalari (API keys, merchant ID) to'g'riligini oldindan tekshirish
        provider.validate_configuration()

        financials = cls.calculate_order_financials(order, chosen_percent=chosen_percent)
        grand_total = financials['grand_total']
        prepayment_percent = financials['prepayment_percent']

        if grand_total <= 0:
            raise ValueError("Buyurtma summasi 0 dan katta bo'lishi kerak")

        # Determine Purpose & Amount
        if purpose == models.Payment.Purpose.BALANCE:
            # Customer paying remaining balance
            payment_purpose = models.Payment.Purpose.BALANCE
            amount = order.remaining_amount
            if amount <= 0:
                raise ValueError("To'lash uchun qoldiq summa mavjud emas (Buyurtma to'liq to'langan).")
        else:
            # Initial Order Checkout Payment (Prepayment / Full)
            if prepayment_percent == 0:
                # 0% prepayment allowed
                payment_purpose = models.Payment.Purpose.FULL
                amount = grand_total
                order.prepayment_percent = 0
                order.prepayment_amount = Decimal('0.00')
            elif prepayment_percent >= 100:
                # 100% full online prepayment
                if provider_key == models.Payment.Provider.CASH:
                    raise ValueError("100% to'lov talab qilinganda naqd to'lov mumkin emas. Iltimos, onlayn to'lov usulini tanlang.")
                payment_purpose = models.Payment.Purpose.FULL
                amount = grand_total
                order.prepayment_percent = 100
                order.prepayment_amount = grand_total
            else:
                # Partial Prepayment (e.g. 30% or 50%)
                if provider_key == models.Payment.Provider.CASH:
                    raise ValueError(f"Oldindan {prepayment_percent}% to'lov talab qilinadi. Iltimos, onlayn to'lov usulini (Click, Payme, Uzum) tanlang.")
                payment_purpose = models.Payment.Purpose.PREPAYMENT
                amount = financials['prepayment_amount']
                order.prepayment_percent = prepayment_percent
                order.prepayment_amount = amount

        if amount <= 0:
            raise ValueError("To'lov summasi 0 dan katta bo'lishi kerak")

        with transaction.atomic():
            order.save(update_fields=['prepayment_percent', 'prepayment_amount'])

            initial_status = models.Payment.Status.PENDING
            if provider_key != models.Payment.Provider.CASH:
                initial_status = models.Payment.Status.INITIATED

            payment = models.Payment.objects.create(
                order=order,
                provider=provider_key,
                purpose=payment_purpose,
                amount=amount,
                currency='UZS',
                status=initial_status,
                payment_method=payment_method
            )

            # If 0% prepayment cash on delivery, mark order accepted immediately and deduct stock once
            if provider_key == models.Payment.Provider.CASH and payment_purpose == models.Payment.Purpose.FULL:
                if order.status == 1:
                    # Deduct stock atomically
                    for item in order.cart_products.filter(product__isnull=False):
                        models.Product.objects.filter(pk=item.product.pk).update(count=F('count') - item.count)
                    order.status = 2  # Accepted
                    order.financial_status = models.Cart.FinancialStatus.UNPAID
                    order.save(update_fields=['status', 'financial_status'])

                models.OrderStatusHistory.objects.create(
                    order=order,
                    old_status=1,
                    new_status=2,
                    comment="Buyurtma yetkazilganda naqd to'lov usuli bilan rasmiylashtirildi (0% oldindan to'lov)"
                )

                models.AuditLog.objects.create(
                    user=order.user,
                    action="ORDER_CHECKOUT_CASH",
                    details=f"Buyurtma #{str(order.code)[:8]} qabul qilindi. Summa: {payment.amount} UZS (Yetkazilganda to'lash)"
                )

            checkout_url = provider.generate_checkout_url(payment, request)
            logger.info("Payment created #%s for Order #%s via %s (Purpose: %s, Amount: %s), checkout_url=%s",
                        str(payment.code)[:8], str(order.code)[:8], provider_key, payment_purpose, amount, checkout_url)

        return payment, checkout_url

    @classmethod
    def settle_cash_balance(
        cls,
        order: models.Cart,
        amount: Optional[Decimal] = None,
        user: Optional[models.User] = None,
        comment: str = ""
    ) -> models.Payment:
        """
        Yetkazib berish vaqtida qoldiq summani naqd (yoki kuryer terminali) orqali to'langan deb rasmiylashtirish.
        """
        remaining = order.remaining_amount
        if remaining <= 0:
            raise ValueError("Ushbu buyurtmada to'lanmagan qoldiq mavjud emas (Buyurtma to'liq to'langan).")

        settle_amount = amount if amount is not None else remaining
        if settle_amount <= 0:
            raise ValueError("Qoldiq to'lov summasi 0 dan katta bo'lishi kerak.")
        if settle_amount > remaining:
            raise ValueError(f"Kiritilgan summa ({settle_amount} UZS) mavjud qoldiqdan ({remaining} UZS) ortiq bo'lishi mumkin emas.")

        with transaction.atomic():
            locked_order = models.Cart.objects.select_for_update().get(pk=order.pk)
            current_remaining = locked_order.remaining_amount
            if current_remaining <= 0:
                raise ValueError("Buyurtma allaqachon to'liq to'langan.")
            if settle_amount > current_remaining:
                settle_amount = current_remaining

            payment = models.Payment.objects.create(
                order=locked_order,
                provider=models.Payment.Provider.CASH,
                purpose=models.Payment.Purpose.BALANCE,
                amount=settle_amount,
                currency='UZS',
                status=models.Payment.Status.PAID,
                paid_at=timezone.now(),
                payment_method='cash_on_delivery'
            )

            # Sync financial status
            cls.sync_order_financial_status(locked_order)

            models.OrderStatusHistory.objects.create(
                order=locked_order,
                old_status=locked_order.status,
                new_status=locked_order.status,
                changed_by=user,
                comment=comment or f"Yetkazib berishda qoldiq to'lov qabul qilindi: {settle_amount} UZS (Naqd/Terminal)"
            )

            models.AuditLog.objects.create(
                user=user or locked_order.user,
                action="BALANCE_PAYMENT_SETTLED",
                details=f"Buyurtma #{str(locked_order.code)[:8]} uchun qoldiq to'landi: {settle_amount} UZS. Yangi holat: {locked_order.get_financial_status_display()}"
            )

        return payment

    @classmethod
    def sync_order_financial_status(cls, order: models.Cart) -> str:
        """
        Buyurtmaning moliyaviy holatini (financial_status) to'langan summalar asosida yangilaydi.
        """
        grand_total = order.grand_total
        paid = order.paid_amount

        if grand_total > 0 and paid >= grand_total:
            order.financial_status = models.Cart.FinancialStatus.FULLY_PAID
        elif paid > 0:
            order.financial_status = models.Cart.FinancialStatus.PARTIALLY_PAID
        else:
            order.financial_status = models.Cart.FinancialStatus.UNPAID

        order.save(update_fields=['financial_status'])
        return order.financial_status

    @classmethod
    def handle_webhook(cls, provider_name: str, request: HttpRequest) -> JsonResponse:
        """
        Provayder callback/webhook so'rovini qabul qilib, mos provayder adapteriga yo'naltiradi.
        """
        provider_key = provider_name.lower()
        provider = cls.get_provider(provider_key)
        if not provider:
            logger.warning("Webhook received for unknown provider: %s", provider_name)
            return JsonResponse({'error': 'Unknown provider'}, status=404)

        return provider.handle_webhook(request)

    @classmethod
    def refund_payment(cls, payment: models.Payment, amount: Optional[float] = None, reason: str = "") -> Dict[str, Any]:
        """
        To'lovni qaytarish (Refund).
        """
        provider = cls.get_provider(payment.provider)
        if not provider:
            return {'success': False, 'message': f"Noma'lum provayder: {payment.provider}"}

        result = provider.refund(payment, amount, reason)
        if result.get('success'):
            if payment.order:
                cls.sync_order_financial_status(payment.order)
            models.AuditLog.objects.create(
                user=payment.order.user if payment.order else None,
                action="PAYMENT_REFUNDED",
                details=f"To'lov #{str(payment.code)[:8]} qaytarildi. Provayder: {payment.provider}. Summa: {payment.refund_amount} UZS. Sabab: {reason}"
            )
        return result
