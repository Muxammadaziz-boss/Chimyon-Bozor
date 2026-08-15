import hashlib
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


class ClickPaymentProvider(BasePaymentProvider):
    provider_name: str = "click"

    # Click Actions
    ACTION_PREPARE = '0'
    ACTION_COMPLETE = '1'

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
        return str(getattr(settings, 'CLICK_SERVICE_ID', '')).strip()

    def get_merchant_id(self) -> str:
        return str(getattr(settings, 'CLICK_MERCHANT_ID', '')).strip()

    def get_secret_key(self) -> str:
        return str(getattr(settings, 'CLICK_SECRET_KEY', '')).strip()

    def get_merchant_user_id(self) -> str:
        return str(getattr(settings, 'CLICK_MERCHANT_USER_ID', '')).strip()

    def is_configured(self) -> bool:
        """
        Click integratsiyasi uchun zarur bo'lgan barcha credentiallar mavjudligi
        va placeholder emasligini tekshiradi.
        """
        service_id = self.get_service_id().lower()
        merchant_id = self.get_merchant_id().lower()
        secret_key = self.get_secret_key().lower()

        if not service_id or not merchant_id or not secret_key:
            return False

        if (service_id in self.PLACEHOLDER_CREDENTIALS or
            merchant_id in self.PLACEHOLDER_CREDENTIALS or
            secret_key in self.PLACEHOLDER_CREDENTIALS):
            return False

        return True

    def validate_configuration(self) -> None:
        """
        Click sozlamalari to'liq va to'g'ri ekanligini tasdiqlaydi.
        Aks holda PaymentConfigurationError chiqaradi.
        """
        if not self.is_configured():
            raise PaymentConfigurationError(
                "Click to'lov tizimi sozlamalari (CLICK_SERVICE_ID, CLICK_MERCHANT_ID, CLICK_SECRET_KEY) "
                "to'liq kiritilmagan yoki test/placeholder holatida. "
                "Iltimos, server muhitida (Environment variables) haqiqiy merchant parametrlarini sozlang."
            )

    def generate_checkout_url(self, payment, request: Optional[HttpRequest] = None) -> str:
        """
        Click Checkout Redirect URL yaratish (Click Payment Form URL).
        Faqat haqiqiy va to'g'ri credentiallar mavjud bo'lganda ishlaydi.
        """
        self.validate_configuration()

        if not payment or payment.amount <= Decimal('0.00'):
            raise ValueError("To'lov summasi 0 dan katta bo'lishi shart.")

        service_id = self.get_service_id()
        merchant_id = self.get_merchant_id()
        amount = f"{payment.amount:.2f}"

        # Production-grade HTTPS Return URL yaratish
        if request:
            return_url = request.build_absolute_uri(f"/payment/success/{payment.order.code}/")
            if not getattr(settings, 'DEBUG', False) and return_url.startswith('http://'):
                return_url = 'https://' + return_url[7:]
        else:
            site_url = getattr(settings, 'SITE_URL', 'https://chimyon-bozor.uz').rstrip('/')
            return_url = f"{site_url}/payment/success/{payment.order.code}/"

        params = {
            'service_id': service_id,
            'merchant_id': merchant_id,
            'amount': amount,
            'transaction_param': str(payment.code),
            'return_url': return_url,
        }

        merchant_user_id = self.get_merchant_user_id()
        if merchant_user_id and merchant_user_id.lower() not in self.PLACEHOLDER_CREDENTIALS:
            params['merchant_user_id'] = merchant_user_id

        checkout_url = f"https://my.click.uz/services/pay?{urlencode(params)}"
        logger.info("Generated Click checkout URL for payment #%s: %s", payment.code, checkout_url)
        return checkout_url

    def verify_signature(self, data: Dict[str, Any], is_complete: bool = False) -> bool:
        """
        Click imzosi (sign_string) to'g'riligini MD5 orqali tekshirish.
        """
        if not self.is_configured():
            logger.warning("Click verify_signature failed: Provider is not properly configured with secret key.")
            return False

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

            # Confirm order status & deduct stock ONCE
            order = locked_payment.order
            if order.status == 1:
                for item in order.cart_products.filter(product__isnull=False):
                    models.Product.objects.filter(pk=item.product.pk).update(count=F('count') - item.count)
                order.status = 2  # Accepted / New
                order.save(update_fields=['status'])

            # Sync financial status
            from .manager import PaymentManager
            PaymentManager.sync_order_financial_status(order)

            models.OrderStatusHistory.objects.create(
                order=order,
                old_status=1,
                new_status=order.status,
                comment=f"Click orqali to'lov ({locked_payment.get_purpose_display()}) muvaffaqiyatli qabul qilindi. Tranzaksiya ID: {click_trans_id}"
            )

            models.AuditLog.objects.create(
                user=order.user,
                action="PAYMENT_SUCCESS_CLICK",
                details=f"Buyurtma #{str(order.code)[:8]} uchun Click to'lovi: {locked_payment.amount} UZS ({locked_payment.get_purpose_display()}). Tranzaksiya #{click_trans_id}"
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
        return {'success': True, 'message': "To'lov qaytarildi (Refund muvaffaqiyatli)."}
