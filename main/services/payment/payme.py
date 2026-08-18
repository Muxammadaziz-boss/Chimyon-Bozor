import base64
import json
import logging
from decimal import Decimal
from typing import Dict, Any, Optional

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from main import models
from .base import BasePaymentProvider, PaymentConfigurationError

logger = logging.getLogger(__name__)


class PaymePaymentProvider(BasePaymentProvider):
    provider_name: str = "payme"

    # Payme JSON-RPC Error Codes
    ERROR_AUTH_FAILED = -32504
    ERROR_INVALID_JSON = -32700
    ERROR_METHOD_NOT_FOUND = -32601
    ERROR_INVALID_PARAMS = -32602
    ERROR_ORDER_NOT_FOUND = -31001
    ERROR_AMOUNT_MISMATCH = -31001
    ERROR_CANNOT_PERFORM = -31008
    ERROR_TRANSACTION_NOT_FOUND = -31003

    def get_merchant_id(self) -> str:
        return str(getattr(settings, 'PAYME_MERCHANT_ID', '')).strip()

    def get_secret_key(self) -> str:
        return str(getattr(settings, 'PAYME_SECRET_KEY', '')).strip()

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
                "Payme to'lov tizimi sozlamalari (PAYME_MERCHANT_ID, PAYME_SECRET_KEY) "
                "to'liq kiritilmagan yoki test holatida."
            )

    def generate_checkout_url(self, payment, request: Optional[HttpRequest] = None) -> str:
        """
        Payme Checkout Form URL:
        m={merchant_id};ac.order_id={payment.code};a={amount_in_tiyin}
        encoded in base64.
        """
        self.validate_configuration()

        if not payment or payment.amount <= Decimal('0.00'):
            raise ValueError("To'lov summasi 0 dan katta bo'lishi shart.")

        merchant_id = self.get_merchant_id()
        amount_tiyin = int(payment.amount * 100)
        params = f"m={merchant_id};ac.order_id={payment.code};a={amount_tiyin}"
        encoded_params = base64.b64encode(params.encode('utf-8')).decode('utf-8')
        return f"https://checkout.paycom.uz/{encoded_params}"

    def verify_auth(self, request: HttpRequest) -> bool:
        """
        Payme Basic Auth: 'Basic <base64(Paycom:secret_key)>'
        """
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Basic '):
            return False
        try:
            encoded_credentials = auth_header.split(' ')[1]
            decoded = base64.b64decode(encoded_credentials).decode('utf-8')
            username, password = decoded.split(':', 1)
            return password == self.get_secret_key()
        except Exception:
            return False

    def handle_webhook(self, request: HttpRequest) -> JsonResponse:
        """
        Payme JSON-RPC webhook endpoint.
        """
        if not self.verify_auth(request):
            logger.warning("Payme webhook: Authentication failed")
            return self._error_response(self.ERROR_AUTH_FAILED, "Authentication failed", None)

        try:
            body = json.loads(request.body.decode('utf-8'))
        except Exception:
            return self._error_response(self.ERROR_INVALID_JSON, "Invalid JSON body", None)

        req_id = body.get('id')
        method = body.get('method')
        params = body.get('params', {})

        logger.info("Payme webhook received: method=%s, req_id=%s", method, req_id)

        if method == 'CheckPerformTransaction':
            return self._check_perform_transaction(params, req_id)
        elif method == 'CreateTransaction':
            return self._create_transaction(params, req_id)
        elif method == 'PerformTransaction':
            return self._perform_transaction(params, req_id)
        elif method == 'CancelTransaction':
            return self._cancel_transaction(params, req_id)
        elif method == 'CheckTransaction':
            return self._check_transaction(params, req_id)
        else:
            return self._error_response(self.ERROR_METHOD_NOT_FOUND, "Method not found", req_id)

    def _check_perform_transaction(self, params: Dict[str, Any], req_id: Any) -> JsonResponse:
        account = params.get('account', {})
        order_code = account.get('order_id')
        amount_tiyin = params.get('amount')

        payment = models.Payment.objects.filter(code=order_code).select_related('order').first()
        if not payment:
            return self._error_response(self.ERROR_ORDER_NOT_FOUND, "Order/Payment not found", req_id)

        expected_tiyin = int(payment.amount * 100)
        if amount_tiyin != expected_tiyin:
            return self._error_response(self.ERROR_AMOUNT_MISMATCH, "Incorrect amount", req_id)

        return JsonResponse({
            'result': {
                'allow': True
            },
            'id': req_id
        })

    def _create_transaction(self, params: Dict[str, Any], req_id: Any) -> JsonResponse:
        payme_trans_id = params.get('id')
        account = params.get('account', {})
        order_code = account.get('order_id')
        amount_tiyin = params.get('amount')
        create_time = params.get('time', int(timezone.now().timestamp() * 1000))

        payment = models.Payment.objects.filter(code=order_code).select_related('order').first()
        if not payment:
            return self._error_response(self.ERROR_ORDER_NOT_FOUND, "Order/Payment not found", req_id)

        expected_tiyin = int(payment.amount * 100)
        if amount_tiyin != expected_tiyin:
            return self._error_response(self.ERROR_AMOUNT_MISMATCH, "Incorrect amount", req_id)

        payment.transaction_id = payme_trans_id
        payment.status = models.Payment.Status.INITIATED
        payment.provider = models.Payment.Provider.PAYME
        payment.provider_response = params
        payment.save(update_fields=['transaction_id', 'status', 'provider', 'provider_response', 'updated_at'])

        return JsonResponse({
            'result': {
                'create_time': create_time,
                'transaction': str(payment.id),
                'state': 1
            },
            'id': req_id
        })

    def _perform_transaction(self, params: Dict[str, Any], req_id: Any) -> JsonResponse:
        payme_trans_id = params.get('id')
        payment = models.Payment.objects.filter(transaction_id=payme_trans_id).select_related('order').first()
        if not payment:
            return self._error_response(self.ERROR_TRANSACTION_NOT_FOUND, "Transaction not found", req_id)

        with transaction.atomic():
            locked = models.Payment.objects.select_for_update().get(pk=payment.pk)
            now_ms = int(timezone.now().timestamp() * 1000)

            if locked.status == models.Payment.Status.PAID:
                # Idempotent response
                return JsonResponse({
                    'result': {
                        'transaction': str(locked.id),
                        'perform_time': int(locked.paid_at.timestamp() * 1000) if locked.paid_at else now_ms,
                        'state': 2
                    },
                    'id': req_id
                })

            locked.status = models.Payment.Status.PAID
            locked.paid_at = timezone.now()
            locked.provider_response = params
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
                comment=f"Payme orqali to'lov ({locked.get_purpose_display()}) muvaffaqiyatli amalga oshirildi. ID: {payme_trans_id}"
            )

            models.AuditLog.objects.create(
                user=order.user,
                action="PAYMENT_SUCCESS_PAYME",
                details=f"Buyurtma #{order.code[:8]} uchun Payme to'lovi: {locked.amount} UZS ({locked.get_purpose_display()}). Tranzaksiya #{payme_trans_id}"
            )

        return JsonResponse({
            'result': {
                'transaction': str(payment.id),
                'perform_time': now_ms,
                'state': 2
            },
            'id': req_id
        })

    def _cancel_transaction(self, params: Dict[str, Any], req_id: Any) -> JsonResponse:
        payme_trans_id = params.get('id')
        payment = models.Payment.objects.filter(transaction_id=payme_trans_id).select_related('order').first()
        if not payment:
            return self._error_response(self.ERROR_TRANSACTION_NOT_FOUND, "Transaction not found", req_id)

        now_ms = int(timezone.now().timestamp() * 1000)
        with transaction.atomic():
            locked = models.Payment.objects.select_for_update().get(pk=payment.pk)
            if locked.status == models.Payment.Status.PAID:
                locked.status = models.Payment.Status.REFUNDED
                locked.refund_amount = locked.amount
                locked.refunded_at = timezone.now()
                state = -2
            else:
                locked.status = models.Payment.Status.CANCELLED
                state = -1
            locked.save()
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

        return JsonResponse({
            'result': {
                'transaction': str(payment.id),
                'cancel_time': now_ms,
                'state': state
            },
            'id': req_id
        })

    def _check_transaction(self, params: Dict[str, Any], req_id: Any) -> JsonResponse:
        payme_trans_id = params.get('id')
        payment = models.Payment.objects.filter(transaction_id=payme_trans_id).first()
        if not payment:
            return self._error_response(self.ERROR_TRANSACTION_NOT_FOUND, "Transaction not found", req_id)

        state = 1
        if payment.status == models.Payment.Status.PAID:
            state = 2
        elif payment.status == models.Payment.Status.CANCELLED:
            state = -1
        elif payment.status == models.Payment.Status.REFUNDED:
            state = -2

        create_time = int(payment.created_at.timestamp() * 1000)
        perform_time = int(payment.paid_at.timestamp() * 1000) if payment.paid_at else 0
        cancel_time = int(payment.refunded_at.timestamp() * 1000) if payment.refunded_at else 0

        return JsonResponse({
            'result': {
                'create_time': create_time,
                'perform_time': perform_time,
                'cancel_time': cancel_time,
                'transaction': str(payment.id),
                'state': state,
                'reason': None
            },
            'id': req_id
        })

    def _error_response(self, code: int, message: str, req_id: Any) -> JsonResponse:
        return JsonResponse({
            'error': {
                'code': code,
                'message': {
                    'ru': message,
                    'uz': message,
                    'en': message
                }
            },
            'id': req_id
        })

    def check_status(self, payment) -> Dict[str, Any]:
        return {
            'provider': 'payme',
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
        return {'success': True, 'message': "Payme to'lovi muvaffaqiyatli qaytarildi."}
