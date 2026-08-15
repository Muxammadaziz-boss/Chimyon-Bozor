from decimal import Decimal
from django.db import models
from django.db.models import Sum
from uuid import uuid4

from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    phone = models.CharField(max_length=150, null= True, blank=True)
    address = models.TextField(null=True, blank=True)
    photo = models.ImageField(upload_to='users', null=True, blank=True)
    phone_verified = models.BooleanField(default=False)

    @property
    def get_avatar_url(self):
        if self.photo and hasattr(self.photo, 'url') and self.photo.name:
            return self.photo.url
        avatar_styles = ['adventurer', 'avataaars', 'bottts', 'fun-emoji', 'lorelei', 'miniavs', 'open-peeps', 'personas']
        seed_hash = sum(ord(c) for c in (self.username or 'user'))
        style = avatar_styles[seed_hash % len(avatar_styles)]
        return f"https://api.dicebear.com/7.x/{style}/svg?seed={self.username}"

    @property
    def cart_items_count(self):
        return CartProduct.objects.filter(cart__user=self, cart__status=1).aggregate(total=Sum('count'))['total'] or 0

    @property
    def wishlist_count(self):
        return WishList.objects.filter(user=self, product__isnull=False).count()

    class Meta(AbstractUser.Meta):
        swappable = "AUTH_USER_MODEL"


class OTPCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='otp_codes')
    phone = models.CharField(max_length=50)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        if self.is_used:
            return False
        from django.utils import timezone
        return (timezone.now() - self.created_at).total_seconds() <= 300

    def __str__(self):
        return f"{self.phone} -> {self.code}"

    class Meta:
        verbose_name = "OTP Kod"
        verbose_name_plural = "OTP Kodlar"




class Code(models.Model):
    code = models.CharField(max_length=150, unique=True, default=uuid4, blank=True, null=True)


    def __str__(self):
        return self.code

    class Meta:
        abstract = True

class Category(models.Model):
    logo = models.ImageField(upload_to='categories')
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=False,null=True,blank=True)
    def __str__(self):
        return self.name



class Product(Code):
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    image = models.ImageField(upload_to='products')
    name = models.CharField(max_length=150)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    discount_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    discount_status = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    count = models.IntegerField(default=0)

    @property
    def active_price(self):
        if self.discount_status and self.discount_price is not None:
            return self.discount_price
        return self.price

    @property
    def discount_percent(self):
        if self.discount_status and self.discount_price and self.price > 0 and self.discount_price < self.price:
            return int(round(((self.price - self.discount_price) / self.price) * 100))
        return 0

    @property
    def is_low_stock(self):
        return 0 < self.count <= 5

    @property
    def is_out_of_stock(self):
        return self.count <= 0

    def __str__(self):
        return self.name


class Service(models.Model):
    name = models.CharField(max_length=150)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to='services', null=True, blank=True)
    duration = models.IntegerField(help_text="Service duration in minutes", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Services"


class Banner(models.Model):
    title = models.CharField(max_length=150)
    image = models.ImageField(upload_to='banners')
    description = models.TextField()
    product_1 = models.ForeignKey(Product, on_delete=models.SET_NULL , null=True, blank=True)

    def __str__(self):
        return self.title

CART_STATUS = (
    (1, 'Faol savat'),
    (2, "Qabul qilindi"),
    (3, "Yo'lda"),
    (4, "Yetkazilgan"),
    (5, "Qaytarilgan")
)

class Cart(Code):
    class FinancialStatus(models.TextChoices):
        UNPAID = 'unpaid', 'To\'lanmagan'
        PARTIALLY_PAID = 'partially_paid', 'Qisman to\'langan'
        FULLY_PAID = 'fully_paid', 'To\'liq to\'langan'
        REFUNDED = 'refunded', 'Qaytarilgan'

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.IntegerField(choices=CART_STATUS, default=1)
    financial_status = models.CharField(
        max_length=20,
        choices=FinancialStatus.choices,
        default=FinancialStatus.UNPAID,
        db_index=True
    )
    prepayment_percent = models.IntegerField(default=0)
    prepayment_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user} - {self.status} ({self.get_financial_status_display()})'

    @property
    def cart_products(self):
        return self.cartproduct_set.select_related('product')

    @property
    def total_price(self):
        return sum(item.total_price for item in self.cart_products)

    @property
    def grand_total(self):
        return Decimal(str(self.total_price))

    @property
    def count_product(self):
        return sum(item.count for item in self.cart_products)

    @property
    def paid_amount(self):
        paid_sum = Decimal('0.00')
        for p in self.payments.filter(status__in=[Payment.Status.PAID, Payment.Status.REFUNDED]):
            paid_sum += Decimal(str(p.amount))
            if p.refund_amount:
                paid_sum -= Decimal(str(p.refund_amount))
        return max(Decimal('0.00'), paid_sum)

    @property
    def remaining_amount(self):
        rem = self.grand_total - self.paid_amount
        return max(Decimal('0.00'), rem)

    @property
    def is_fully_paid(self):
        return self.financial_status == self.FinancialStatus.FULLY_PAID or (self.grand_total > 0 and self.remaining_amount <= 0)

    @property
    def is_partially_paid(self):
        return self.financial_status == self.FinancialStatus.PARTIALLY_PAID


class CartProduct(Code):
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE)
    count = models.IntegerField()

    @property
    def unit_price(self):
        if not self.product:
            return 0
        return self.product.active_price

    @property
    def total_price(self):
        if not self.product:
            return 0
        return self.unit_price * self.count


class WishList(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user} - {self.product}'


class Review(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    rating = models.IntegerField(default=5)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} - {self.product.name} ({self.rating}/5)"

    class Meta:
        ordering = ['-created_at']


class SiteSettings(models.Model):
    site_name = models.CharField(max_length=150, default="Chimyon-bozor")
    tagline = models.CharField(max_length=255, blank=True)
    logo = models.ImageField(upload_to='settings', null=True, blank=True)
    favicon = models.ImageField(upload_to='settings', null=True, blank=True)
    hero_title = models.CharField(max_length=255, blank=True)
    hero_description = models.TextField(blank=True)
    footer_description = models.TextField(blank=True)
    contact_title = models.CharField(max_length=150, blank=True)
    contact_description = models.TextField(blank=True)
    copyright_text = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    facebook = models.URLField(blank=True)
    twitter = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
    linkedin = models.URLField(blank=True)
    telegram = models.URLField(blank=True, help_text="Telegram kanal yoki guruh havolasi (masalan: https://t.me/sizning_kanal)")
    prepayment_enabled = models.BooleanField(default=True, verbose_name="Oldindan to'lov tizimi yoqilgan")
    prepayment_percent = models.IntegerField(
        default=30,
        choices=[
            (0, "0% (Faqat yetkazilganda to'lash)"),
            (30, "30% (Oldindan to'lov)"),
            (50, "50% (Yarmi oldindan)"),
            (100, "100% (To'liq onlayn to'lov)")
        ],
        verbose_name="Oldindan to'lov foizi"
    )
    allow_cash_balance = models.BooleanField(default=True, verbose_name="Yetkazilganda naqd qoldiq to'loviga ruxsat")
    allow_online_balance_payment = models.BooleanField(default=True, verbose_name="Qoldiqni onlayn to'lashga ruxsat")

    def __str__(self):
        return self.site_name

    class Meta:
        verbose_name = "Sayt sozlamalari"
        verbose_name_plural = "Sayt sozlamalari"


class Address(models.Model):
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Manzil"
        verbose_name_plural = "Manzillar"


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='status_history')
    old_status = models.IntegerField(choices=CART_STATUS, null=True, blank=True)
    new_status = models.IntegerField(choices=CART_STATUS)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    comment = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Buyurtma status tarixi"
        verbose_name_plural = "Buyurtma status tarixlari"

    def __str__(self):
        return f"Order #{str(self.order.code)[:8] if self.order and self.order.code else self.order_id}: {self.old_status} -> {self.new_status}"


class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=100)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Audit jurnali"
        verbose_name_plural = "Audit jurnallari"

    def __str__(self):
        return f"{self.user} - {self.action} ({self.created_at.strftime('%d.%m.%Y %H:%M') if self.created_at else ''})"


class Payment(models.Model):
    class Provider(models.TextChoices):
        CLICK = 'click', 'Click'
        PAYME = 'payme', 'Payme'
        UZUM = 'uzum', 'Uzum Bank'
        CASH = 'cash', 'Yetkazilganda naqd to\'lov'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Kutilmoqda'
        INITIATED = 'initiated', 'Boshlandi'
        PAID = 'paid', 'To\'landi'
        FAILED = 'failed', 'Xatolik'
        CANCELLED = 'cancelled', 'Bekor qilindi'
        REFUNDED = 'refunded', 'Qaytarildi'

    class Purpose(models.TextChoices):
        PREPAYMENT = 'prepayment', "Oldindan to'lov"
        BALANCE = 'balance', "Qoldiq to'lov"
        FULL = 'full', "To'liq to'lov"

    code = models.CharField(max_length=150, unique=True, default=uuid4, db_index=True)
    order = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='payments')
    provider = models.CharField(max_length=30, choices=Provider.choices, default=Provider.CASH, db_index=True)
    transaction_id = models.CharField(max_length=255, blank=True, null=True, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='UZS')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    purpose = models.CharField(max_length=20, choices=Purpose.choices, default=Purpose.FULL, db_index=True)
    payment_method = models.CharField(max_length=50, blank=True, default='card')
    provider_response = models.JSONField(blank=True, null=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "To'lov"
        verbose_name_plural = "To'lovlar"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = str(uuid4())
        elif not isinstance(self.code, str):
            self.code = str(self.code)
        super().save(*args, **kwargs)

    def __str__(self):
        code_str = str(self.code)[:8] if self.code else ''
        return f"Payment #{code_str} - {self.get_provider_display()} - {self.amount} {self.currency} ({self.get_status_display()})"

    @property
    def is_paid(self):
        return self.status == self.Status.PAID




