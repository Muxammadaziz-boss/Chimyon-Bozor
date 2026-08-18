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
        payment.status = models.Payment.Status.REFUNDED
        payment.refund_amount = Decimal(str(amount)) if amount else payment.amount
        payment.refunded_at = timezone.now()
        payment.save(update_fields=['status', 'refund_amount', 'refunded_at', 'updated_at'])
        if payment.order and payment.order.paid_amount <= Decimal('0.00'):
            from .manager import PaymentManager
            PaymentManager.release_order_inventory(payment.order)
            if payment.order.status != 1:
                payment.order.status = 5
                payment.order.save(update_fields=['status'])
        return {'success': True, 'message': "Naqd to'lov bekor qilindi / qaytarildi."}
