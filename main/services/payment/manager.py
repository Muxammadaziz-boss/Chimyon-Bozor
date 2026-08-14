import logging
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple

from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from main import models
from .base import BasePaymentProvider
from .click import ClickPaymentProvider
from .payme import PaymePaymentProvider
from .uzum import UzumPaymentProvider
from .cash import CashOnDeliveryProvider

logger = logging.getLogger(__name__)


class PaymentManager:
    """
    Markaziy to'lov menejeri.
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
    def create_payment(
        cls,
        order: models.Cart,
        provider_name: str,
        payment_method: str = 'card',
        request: Optional[HttpRequest] = None
    ) -> Tuple[models.Payment, str]:
        """
        Buyurtma uchun yangi to'lov yozuvi (Payment) yaratadi va to'lov havolasini (checkout URL) qaytaradi.
        """
        provider_key = provider_name.lower()
        provider = cls.get_provider(provider_key)
        if not provider:
            raise ValueError(f"Noma'lum to'lov provayderi: {provider_name}")

        # Server-side Decimal recalculation of order total
        cart_products = order.cart_products.filter(product__isnull=False)
        if not cart_products.exists():
            raise ValueError("Buyurtma savatida mahsulotlar topilmadi")

        total_amount = Decimal('0.00')
        for item in cart_products:
            unit_price = Decimal(str(item.product.active_price))
            if unit_price <= 0:
                raise ValueError(f"Mahsulot narxi noto'g'ri: {item.product.name}")
            if item.count <= 0:
                raise ValueError(f"Mahsulot soni noto'g'ri: {item.product.name}")
            total_amount += unit_price * Decimal(str(item.count))

        if total_amount <= 0:
            raise ValueError("Jami to'lov summasi 0 dan katta bo'lishi kerak")

        with transaction.atomic():
            # Initial status
            initial_status = models.Payment.Status.PENDING
            if provider_key != models.Payment.Provider.CASH:
                initial_status = models.Payment.Status.INITIATED

            payment = models.Payment.objects.create(
                order=order,
                provider=provider_key,
                amount=total_amount,
                currency='UZS',
                status=initial_status,
                payment_method=payment_method
            )

            # If cash on delivery, mark order accepted immediately
            if provider_key == models.Payment.Provider.CASH:
                if order.status == 1:
                    order.status = 2  # Accepted
                    order.save(update_fields=['status'])

                models.OrderStatusHistory.objects.create(
                    order=order,
                    old_status=1,
                    new_status=2,
                    comment="Buyurtma yetkazilganda naqd to'lov usuli bilan rasmiylashtirildi"
                )

                models.AuditLog.objects.create(
                    user=order.user,
                    action="ORDER_CHECKOUT_CASH",
                    details=f"Buyurtma #{order.code[:8]} qabul qilindi. Summa: {payment.amount} UZS (Yetkazilganda to'lash)"
                )

            checkout_url = provider.generate_checkout_url(payment, request)
            logger.info("Payment created #%s for Order #%s via %s, checkout_url=%s",
                        payment.code, order.code, provider_key, checkout_url)

        return payment, checkout_url

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
        To'lovni qaytarish (Refund)
        """
        provider = cls.get_provider(payment.provider)
        if not provider:
            return {'success': False, 'message': f"Noma'lum provayder: {payment.provider}"}

        result = provider.refund(payment, amount, reason)
        if result.get('success'):
            models.AuditLog.objects.create(
                user=payment.order.user if payment.order else None,
                action="PAYMENT_REFUNDED",
                details=f"To'lov #{payment.code[:8]} qaytarildi. Provayder: {payment.provider}. Summa: {payment.refund_amount} UZS. Sabab: {reason}"
            )
        return result
