from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from django.http import HttpRequest, JsonResponse


class BasePaymentProvider(ABC):
    """
    To'lov provayderlari uchun abstrakt baza klassi.
    Barcha provayder adapterlari ushbu interfeysni implement qilishi shart.
    """
    provider_name: str = "base"

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
