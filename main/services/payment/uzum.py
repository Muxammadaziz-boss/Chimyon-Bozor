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
from .base import BasePaymentProvider, PaymentConfigurationError

logger = logging.getLogger(__name__)


class UzumPaymentProvider(BasePaymentProvider):
    provider_name: str = "uzum"

    def get_merchant_id(self) -> str:
        return str(getattr(settings, 'UZUM_MERCHANT_ID', '')).strip()

    def get_secret_key(self) -> str:
        return str(getattr(settings, 'UZUM_SECRET_KEY', '')).strip()

    def is_configured(self) -> bool:
        m_id = self.get_merchant_id().lower()
        s_key = self.get_secret_key().lower()
        if not m_id or not s_key:
            return False
        if m_id in self.PLACEHOLDER_CREDENTIALS or s_key in self.PLACEHOLDER_CREDENTIALS:
            return False
        return True

    def validate_configuration(self) -> None:
        if not self.is_configured():
            raise PaymentConfigurationError(
                "Uzum to'lov tizimi sozlamalari (UZUM_MERCHANT_ID, UZUM_SECRET_KEY) "
                "to'liq kiritilmagan yoki test holatida."
            )

    def generate_checkout_url(self, payment, request: Optional[HttpRequest] = None) -> str:
        """
        Uzum Bank / Uzum Pay Web Checkout Form URL.
        """
        self.validate_configuration()

        if not payment or payment.amount <= Decimal('0.00'):
            raise ValueError("To'lov summasi 0 dan katta bo'lishi shart.")

        merchant_id = self.get_merchant_id()
        amount = f"{payment.amount:.2f}"
        if request:
            return_url = request.build_absolute_uri(f"/payment/success/{payment.order.code}/")
            if not getattr(settings, 'DEBUG', False) and return_url.startswith('http://'):
                return_url = 'https://' + return_url[7:]
        else:
            site_url = getattr(settings, 'SITE_URL', 'https://chimyon-bozor.uz').rstrip('/')
            return_url = f"{site_url}/payment/success/{payment.order.code}/"

        params = {
            'merchantId': merchant_id,
            'orderId': str(payment.code),
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

        if self.is_configured():
            if not signature or not self.verify_signature(raw_body, signature):
                logger.warning("Uzum webhook: Invalid or missing signature")
                return JsonResponse({'status': 'error', 'message': 'Invalid signature'}, status=403)

        event = data.get('event') or data.get('status')
        order_code = data.get('orderId')
        trans_id = str(data.get('transactionId', data.get('id', '')))
        amount_raw = data.get('amount')

        logger.info("Uzum webhook: event=%s, orderId=%s, transId=%s", event, order_code, trans_id)

        payment = models.Payment.objects.filter(code=order_code).select_related('order').first()
        if not payment:
            return JsonResponse({'status': 'error', 'message': 'Payment not found'}, status=404)

        if amount_raw is not None:
            try:
                callback_amt = Decimal(str(amount_raw))
                if abs(payment.amount - callback_amt) > Decimal('0.01'):
                    logger.warning("Uzum webhook: Amount mismatch expected=%s received=%s", payment.amount, callback_amt)
                    return JsonResponse({'status': 'error', 'message': 'Incorrect amount'}, status=400)
            except Exception:
                pass

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
                from .manager import PaymentManager
                PaymentManager.reserve_order_inventory(order)
                if order.status == 1:
                    order.status = 2  # Accepted
                    order.save(update_fields=['status'])

                # Sync financial status
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
            with transaction.atomic():
                locked = models.Payment.objects.select_for_update().get(pk=payment.pk)
                locked.status = models.Payment.Status.FAILED
                locked.error_message = data.get('error', 'Payment failed')
                locked.provider_response = data
                locked.save(update_fields=['status', 'error_message', 'provider_response', 'updated_at'])
                if locked.order:
                    from .manager import PaymentManager
                    PaymentManager.sync_order_financial_status(locked.order)
                    locked.order.refresh_from_db(fields=['financial_status', 'inventory_status', 'status'])
                    if locked.order.paid_amount <= Decimal('0.00'):
                        if locked.order.inventory_status == models.Cart.InventoryStatus.RESERVED:
                            locked.order.release_inventory()
                        if locked.order.status != 1:
                            locked.order.status = 5
                            locked.order.save(update_fields=['status'])
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
        if payment.order and payment.order.paid_amount <= Decimal('0.00'):
            from .manager import PaymentManager
            PaymentManager.release_order_inventory(payment.order)
            if payment.order.status != 1:
                payment.order.status = 5
                payment.order.save(update_fields=['status'])
        return {'success': True, 'message': "Uzum to'lovi qaytarildi."}
