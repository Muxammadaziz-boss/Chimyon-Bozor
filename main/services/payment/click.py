import hashlib
import logging
from decimal import Decimal
from typing import Dict, Any, Optional
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from main import models
from .base import BasePaymentProvider

logger = logging.getLogger(__name__)


class ClickPaymentProvider(BasePaymentProvider):
    provider_name: str = "click"

    # Click Error Codes
    ERROR_SUCCESS = 0
    ERROR_SIGN_CHECK_FAILED = -1
    ERROR_INVALID_AMOUNT = -2
    ERROR_ACTION_NOT_FOUND = -3
    ERROR_ALREADY_PAID = -4
    ERROR_ORDER_NOT_FOUND = -5
    ERROR_TRANSACTION_NOT_FOUND = -6
    ERROR_FAILED_UPDATE = -7
    ERROR_REQUEST_ERROR = -8
    ERROR_TRANSACTION_CANCELLED = -9

    def get_service_id(self) -> str:
        return getattr(settings, 'CLICK_SERVICE_ID', 'test_service_id')

    def get_merchant_id(self) -> str:
        return getattr(settings, 'CLICK_MERCHANT_ID', 'test_merchant_id')

    def get_secret_key(self) -> str:
        return getattr(settings, 'CLICK_SECRET_KEY', 'test_secret_key')

    def generate_checkout_url(self, payment, request: Optional[HttpRequest] = None) -> str:
        """
        Click Checkout Redirect URL yaratish (Click Payment Form URL).
        """
        service_id = self.get_service_id()
        merchant_id = self.get_merchant_id()
        amount = f"{payment.amount:.2f}"
        return_url = ""
        if request:
            return_url = request.build_absolute_uri(f"/payment/success/{payment.order.code}/")

        params = {
            'service_id': service_id,
            'merchant_id': merchant_id,
            'amount': amount,
            'transaction_param': payment.code,
            'return_url': return_url,
        }
        return f"https://my.click.uz/services/pay?{urlencode(params)}"

    def verify_signature(self, data: Dict[str, Any], is_complete: bool = False) -> bool:
        """
        Click imzosi (sign_string) to'g'riligini MD5 orqali tekshirish.
        """
        secret_key = self.get_secret_key()
        click_trans_id = str(data.get('click_trans_id', ''))
        service_id = str(data.get('service_id', ''))
        merchant_trans_id = str(data.get('merchant_trans_id', ''))
        merchant_prepare_id = str(data.get('merchant_prepare_id', '')) if is_complete else ''
        amount = str(data.get('amount', ''))
        action = str(data.get('action', ''))
        sign_time = str(data.get('sign_time', ''))
        sign_string = str(data.get('sign_string', ''))

        if is_complete:
            text = f"{click_trans_id}{service_id}{secret_key}{merchant_trans_id}{merchant_prepare_id}{amount}{action}{sign_time}"
        else:
            text = f"{click_trans_id}{service_id}{secret_key}{merchant_trans_id}{amount}{action}{sign_time}"

        expected_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        return expected_hash.lower() == sign_string.lower()

    def handle_webhook(self, request: HttpRequest) -> JsonResponse:
        """
        Click Shop API (Prepare & Complete) webhook handleri.
        """
        data = request.POST.dict() if request.method == 'POST' else request.GET.dict()
        action = data.get('action')
        click_trans_id = data.get('click_trans_id')
        merchant_trans_id = data.get('merchant_trans_id')  # Payment.code
        amount_raw = data.get('amount')

        logger.info("Click webhook received: action=%s, click_trans_id=%s, merchant_trans_id=%s",
                    action, click_trans_id, merchant_trans_id)

        # Action 0 = Prepare, Action 1 = Complete
        if action == '0':
            return self._handle_prepare(data)
        elif action == '1':
            return self._handle_complete(data)
        else:
            return JsonResponse({
                'error': self.ERROR_ACTION_NOT_FOUND,
                'error_note': 'Action not found'
            })

    def _handle_prepare(self, data: Dict[str, Any]) -> JsonResponse:
        if not self.verify_signature(data, is_complete=False):
            logger.warning("Click Prepare: Invalid signature")
            return JsonResponse({
                'error': self.ERROR_SIGN_CHECK_FAILED,
                'error_note': 'SIGN CHECK FAILED'
            })

        merchant_trans_id = data.get('merchant_trans_id')
        click_trans_id = str(data.get('click_trans_id', ''))
        try:
            amount = Decimal(str(data.get('amount', '0')))
        except Exception:
            return JsonResponse({
                'error': self.ERROR_INVALID_AMOUNT,
                'error_note': 'Incorrect amount'
            })

        payment = models.Payment.objects.filter(code=merchant_trans_id).select_related('order').first()
        if not payment:
            return JsonResponse({
                'error': self.ERROR_ORDER_NOT_FOUND,
                'error_note': 'Payment or Order not found'
            })

        if payment.status == models.Payment.Status.PAID:
            return JsonResponse({
                'error': self.ERROR_ALREADY_PAID,
                'error_note': 'Already paid'
            })

        # Check amount match (within 0.01 precision)
        if abs(payment.amount - amount) > Decimal('0.01'):
            logger.warning("Click Prepare: Amount mismatch expected=%s received=%s", payment.amount, amount)
            return JsonResponse({
                'error': self.ERROR_INVALID_AMOUNT,
                'error_note': 'Incorrect amount'
            })

        # Update payment status to INITIATED
        payment.status = models.Payment.Status.INITIATED
        payment.transaction_id = click_trans_id
        payment.provider = models.Payment.Provider.CLICK
        payment.provider_response = data
        payment.save(update_fields=['status', 'transaction_id', 'provider', 'provider_response', 'updated_at'])

        return JsonResponse({
            'click_trans_id': click_trans_id,
            'merchant_trans_id': merchant_trans_id,
            'merchant_prepare_id': payment.id,
            'error': self.ERROR_SUCCESS,
            'error_note': 'Success'
        })

    def _handle_complete(self, data: Dict[str, Any]) -> JsonResponse:
        if not self.verify_signature(data, is_complete=True):
            logger.warning("Click Complete: Invalid signature")
            return JsonResponse({
                'error': self.ERROR_SIGN_CHECK_FAILED,
                'error_note': 'SIGN CHECK FAILED'
            })

        merchant_trans_id = data.get('merchant_trans_id')
        click_trans_id = str(data.get('click_trans_id', ''))
        error_code = int(data.get('error', 0))

        payment = models.Payment.objects.filter(code=merchant_trans_id).select_related('order').first()
        if not payment:
            return JsonResponse({
                'error': self.ERROR_ORDER_NOT_FOUND,
                'error_note': 'Payment or Order not found'
            })

        # If click sent a payment failure or cancellation
        if error_code < 0:
            payment.status = models.Payment.Status.FAILED
            payment.error_message = data.get('error_note', f'Click error {error_code}')
            payment.provider_response = data
            payment.save(update_fields=['status', 'error_message', 'provider_response', 'updated_at'])
            return JsonResponse({
                'error': self.ERROR_TRANSACTION_CANCELLED,
                'error_note': 'Transaction cancelled'
            })

        # Idempotent Atomic Completion
        with transaction.atomic():
            locked_payment = models.Payment.objects.select_for_update().get(pk=payment.pk)
            if locked_payment.status == models.Payment.Status.PAID:
                # Already processed safely
                return JsonResponse({
                    'click_trans_id': click_trans_id,
                    'merchant_trans_id': merchant_trans_id,
                    'merchant_confirm_id': locked_payment.id,
                    'error': self.ERROR_SUCCESS,
                    'error_note': 'Already confirmed'
                })

            locked_payment.status = models.Payment.Status.PAID
            locked_payment.paid_at = timezone.now()
            locked_payment.transaction_id = click_trans_id
            locked_payment.provider_response = data
            locked_payment.save()

            # Confirm order status
            order = locked_payment.order
            if order.status == 1:
                order.status = 2  # Accepted / New
                order.save(update_fields=['status'])

            models.OrderStatusHistory.objects.create(
                order=order,
                old_status=1,
                new_status=order.status,
                comment=f"Click orqali to'lov muvaffaqiyatli qabul qilindi. Tranzaksiya ID: {click_trans_id}"
            )

            models.AuditLog.objects.create(
                user=order.user,
                action="PAYMENT_SUCCESS_CLICK",
                details=f"Buyurtma #{order.code[:8]} uchun Click to'lovi: {locked_payment.amount} UZS. Tranzaksiya #{click_trans_id}"
            )

        logger.info("Click payment #%s completed successfully.", payment.code)
        return JsonResponse({
            'click_trans_id': click_trans_id,
            'merchant_trans_id': merchant_trans_id,
            'merchant_confirm_id': payment.id,
            'error': self.ERROR_SUCCESS,
            'error_note': 'Success'
        })

    def check_status(self, payment) -> Dict[str, Any]:
        return {
            'provider': 'click',
            'status': payment.status,
            'is_paid': payment.is_paid,
            'transaction_id': payment.transaction_id,
            'amount': float(payment.amount),
        }

    def refund(self, payment, amount: Optional[float] = None, reason: str = "") -> Dict[str, Any]:
        if not payment.is_paid:
            return {'success': False, 'message': "Faqat to'langan to'lovlarni qaytarish mumkin."}
        payment.status = models.Payment.Status.REFUNDED
        payment.refund_amount = Decimal(str(amount)) if amount else payment.amount
        payment.refunded_at = timezone.now()
        payment.save(update_fields=['status', 'refund_amount', 'refunded_at', 'updated_at'])
        return {'success': True, 'message': "To'lov qaytarildi (Refund muvaffaqiyatli)."}
