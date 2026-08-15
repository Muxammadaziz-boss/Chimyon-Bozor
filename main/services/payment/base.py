from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from django.http import HttpRequest, JsonResponse


class PaymentError(Exception):
    """To'lov tizimlari uchun umumiy xatolik bazaviy klassi."""
    pass


class PaymentConfigurationError(PaymentError):
    """To'lov provayderi sozlamalari (API kalitlari, merchant ID) to'liq yoki to'g'ri sozlanmagan holat."""
    pass


class PaymentProcessingError(PaymentError):
    """To'lov jarayonida yuz bergan xatolik."""
    pass


class BasePaymentProvider(ABC):
    """
    To'lov provayderlari uchun abstrakt baza klassi.
    Barcha provayder adapterlari ushbu interfeysni implement qilishi shart.
    """
    provider_name: str = "base"

    PLACEHOLDER_CREDENTIALS = {
        '', 'none', 'null', 'undefined', 'test',
        'test_service_id', 'test_merchant_id', 'test_secret_key', 'test_click_secret',
        'test_payme_merchant_id', 'test_payme_secret',
        'test_uzum_merchant_id', 'test_uzum_secret',
        'your_click_service_id', 'your_merchant_id', 'your_secret_key'
    }

    def is_configured(self) -> bool:
        """
        Provayderning production yoki haqiqiy API kalitlari sozlanganligini tekshiradi.
        """
        return True

    def validate_configuration(self) -> None:
        """
        Agar provayder to'g'ri sozlanmagan bo'lsa PaymentConfigurationError chiqaradi.
        """
        if not self.is_configured():
            raise PaymentConfigurationError(
                f"{self.provider_name.capitalize()} to'lov tizimi sozlamalari to'liq kiritilmagan yoki test holatida."
            )

    @abstractmethod
    def generate_checkout_url(self, payment, request: Optional[HttpRequest] = None) -> str:
        """
        Mijoz to'lovni amalga oshirishi uchun provayder checkout URL manzilini hosil qiladi.
        """
        pass

    @abstractmethod
    def handle_webhook(self, request: HttpRequest) -> JsonResponse:
        """
        Provayderdan kelgan callback/webhook so'rovini qabul qiladi va xavfsiz qayta ishlaydi.
        """
        pass

    @abstractmethod
    def check_status(self, payment) -> Dict[str, Any]:
        """
        To'lov holatini provayder serveri orqali tekshiradi.
        """
        pass

    @abstractmethod
    def refund(self, payment, amount: Optional[float] = None, reason: str = "") -> Dict[str, Any]:
        """
        To'langan mablag'ni qaytarish (refund) operatsiyasini bajaradi.
        """
        pass

