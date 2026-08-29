import logging
from decimal import Decimal
from typing import Dict, Any, Optional

from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from main import models
from .base import BasePaymentProvider

logger = logging.getLogger(__name__)


class CashOnDeliveryProvider(BasePaymentProvider):
    provider_name: str = "cash"

    def generate_checkout_url(self, payment, request: Optional[HttpRequest] = None) -> str:
        """
        Naqd to'lov uchun to'g'ridan-to'g'ri buyurtma muvaffaqiyat sahifasi URL manzili.
        """
        return f"/payment/success/{payment.order.code}/"

    def handle_webhook(self, request: HttpRequest) -> JsonResponse:
        return JsonResponse({'status': 'not_applicable', 'message': "Naqd to'lov uchun webhook mavjud emas."})

    def check_status(self, payment) -> Dict[str, Any]:
        return {
            'provider': 'cash',
            'status': payment.status,
            'is_paid': payment.is_paid,
            'amount': float(payment.amount),
            'method': 'Yetkazilganda naqd to\'lov'
        }

    def refund(self, payment, amount: Optional[float] = None, reason: str = "") -> Dict[str, Any]:
        with transaction.atomic():
            locked = models.Payment.objects.select_for_update().get(pk=payment.pk)
            try:
                refund_dec = Decimal(str(amount)) if amount is not None else locked.amount
            except (ValueError, TypeError):
                return {'success': False, 'message': "Noto'g'ri summa."}
            if refund_dec <= Decimal('0.00'):
                return {'success': False, 'message': "Qaytarish summasi 0 dan katta bo'lishi kerak."}

            current_refund = locked.refund_amount or Decimal('0.00')
            refundable = locked.amount - current_refund
            if refund_dec > refundable:
                return {'success': False, 'message': "Qaytarish summasi to'lov summasidan ortiq bo'lishi mumkin emas."}

            locked.refund_amount = current_refund + refund_dec
            if locked.refund_amount >= locked.amount:
                locked.status = models.Payment.Status.REFUNDED
            locked.refunded_at = timezone.now()
            locked.save(update_fields=['status', 'refund_amount', 'refunded_at', 'updated_at'])
            return {'success': True, 'message': "Naqd to'lov bekor qilindi / qaytarildi."}
