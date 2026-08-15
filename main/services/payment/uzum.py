import hmac
import hashlib
import json
import logging
from decimal import Decimal
from typing import Dict, Any, Optional
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from main import models
from .base import BasePaymentProvider

logger = logging.getLogger(__name__)


class UzumPaymentProvider(BasePaymentProvider):
    provider_name: str = "uzum"

    def get_merchant_id(self) -> str:
        return getattr(settings, 'UZUM_MERCHANT_ID', 'test_uzum_merchant_id')

    def get_secret_key(self) -> str:
        return getattr(settings, 'UZUM_SECRET_KEY', 'test_uzum_secret_key')

    def generate_checkout_url(self, payment, request: Optional[HttpRequest] = None) -> str:
        """
        Uzum Bank / Uzum Pay Web Checkout Form URL.
        """
        merchant_id = self.get_merchant_id()
        amount = f"{payment.amount:.2f}"
        return_url = ""
        if request:
            return_url = request.build_absolute_uri(f"/payment/success/{payment.order.code}/")

        params = {
            'merchantId': merchant_id,
            'orderId': payment.code,
            'amount': amount,
            'currency': 'UZS',
            'returnUrl': return_url,
        }
        return f"https://pay.uzumbank.uz/checkout?{urlencode(params)}"

    def verify_signature(self, payload: str, signature: str) -> bool:
        """
        Uzum HMAC SHA256 signature check.
        """
        secret_key = self.get_secret_key()
        computed = hmac.new(secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed.lower(), signature.lower())

    def handle_webhook(self, request: HttpRequest) -> JsonResponse:
        """
        Uzum Bank webhook callback handler.
        """
        signature = request.headers.get('X-Signature', '')
        try:
            raw_body = request.body.decode('utf-8')
            data = json.loads(raw_body)
        except Exception:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)

        if signature and not self.verify_signature(raw_body, signature):
            logger.warning("Uzum webhook: Invalid signature")
            return JsonResponse({'status': 'error', 'message': 'Invalid signature'}, status=403)

        event = data.get('event') or data.get('status')
        order_code = data.get('orderId')
        trans_id = str(data.get('transactionId', data.get('id', '')))
        amount_raw = data.get('amount')

        logger.info("Uzum webhook: event=%s, orderId=%s, transId=%s", event, order_code, trans_id)

        payment = models.Payment.objects.filter(code=order_code).select_related('order').first()
        if not payment:
            return JsonResponse({'status': 'error', 'message': 'Payment not found'}, status=404)

        if event in ('SUCCESS', 'PAID', 'CONFIRMED'):
            with transaction.atomic():
                locked = models.Payment.objects.select_for_update().get(pk=payment.pk)
                if locked.status == models.Payment.Status.PAID:
                    return JsonResponse({'status': 'ok', 'message': 'Already processed'})

                locked.status = models.Payment.Status.PAID
                locked.paid_at = timezone.now()
                locked.transaction_id = trans_id or locked.transaction_id
                locked.provider = models.Payment.Provider.UZUM
                locked.provider_response = data
                locked.save()

                order = locked.order
                if order.status == 1:
                    for item in order.cart_products.filter(product__isnull=False):
                        models.Product.objects.filter(pk=item.product.pk).update(count=F('count') - item.count)
                    order.status = 2  # Accepted
                    order.save(update_fields=['status'])

                # Sync financial status
                from .manager import PaymentManager
                PaymentManager.sync_order_financial_status(order)

                models.OrderStatusHistory.objects.create(
                    order=order,
                    old_status=1,
                    new_status=order.status,
                    comment=f"Uzum Bank orqali to'lov ({locked.get_purpose_display()}) muvaffaqiyatli qabul qilindi. ID: {trans_id}"
                )

                models.AuditLog.objects.create(
                    user=order.user,
                    action="PAYMENT_SUCCESS_UZUM",
                    details=f"Buyurtma #{order.code[:8]} uchun Uzum Bank to'lovi: {locked.amount} UZS ({locked.get_purpose_display()}). Tranzaksiya #{trans_id}"
                )

            return JsonResponse({'status': 'ok', 'message': 'Payment confirmed'})

        elif event in ('FAILED', 'CANCELED', 'REJECTED'):
            payment.status = models.Payment.Status.FAILED
            payment.error_message = data.get('error', 'Payment failed')
            payment.provider_response = data
            payment.save(update_fields=['status', 'error_message', 'provider_response', 'updated_at'])
            return JsonResponse({'status': 'ok', 'message': 'Failure acknowledged'})

        return JsonResponse({'status': 'ok', 'message': 'Event processed'})

    def check_status(self, payment) -> Dict[str, Any]:
        return {
            'provider': 'uzum',
            'status': payment.status,
            'is_paid': payment.is_paid,
            'transaction_id': payment.transaction_id,
            'amount': float(payment.amount),
        }

    def refund(self, payment, amount: Optional[float] = None, reason: str = "") -> Dict[str, Any]:
        if not payment.is_paid:
            return {'success': False, 'message': "Faqat to'langan to'lovlarni qaytarish mumkin."}
        refund_dec = Decimal(str(amount)) if amount else payment.amount
        if refund_dec > payment.amount:
            return {'success': False, 'message': "Qaytarish summasi to'lov summasidan ortiq bo'lishi mumkin emas."}
        if refund_dec <= 0:
            return {'success': False, 'message': "Qaytarish summasi 0 dan katta bo'lishi kerak."}

        payment.refund_amount = (payment.refund_amount or Decimal('0.00')) + refund_dec
        if payment.refund_amount >= payment.amount:
            payment.status = models.Payment.Status.REFUNDED
        payment.refunded_at = timezone.now()
        payment.save(update_fields=['status', 'refund_amount', 'refunded_at', 'updated_at'])
        return {'success': True, 'message': "Uzum to'lovi qaytarildi."}
