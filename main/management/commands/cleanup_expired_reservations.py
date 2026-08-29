from django.core.management.base import BaseCommand
from main.models import Cart


class Command(BaseCommand):
    help = "Muddati o'tgan band qilingan tovarlarni bekor qilib omborga qaytarish (Expired inventory reservations cleanup)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--timeout',
            type=int,
            default=15,
            help="Bandlik muddati (daqiqalarda, default: 15)"
        )

    def handle(self, *args, **options):
        timeout_minutes = options.get('timeout', 15)
        self.stdout.write(f"Muddati o'tgan buyurtmalarni tekshirish boshlandi (timeout: {timeout_minutes} daqiqa)...")
        released_count = Cart.cleanup_expired_reservations(timeout_minutes=timeout_minutes)
        self.stdout.write(self.style.SUCCESS(f"Muvaffaqiyatli yakunlandi: {released_count} ta buyurtma ombor zaxirasiga qaytarildi."))
