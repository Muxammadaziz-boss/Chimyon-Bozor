from django.db import models
from django.db.models import Sum
from uuid import uuid4

from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    phone = models.CharField(max_length=150, null= True, blank=True)
    address = models.TextField(null=True, blank=True)
    photo = models.ImageField(upload_to='users', null=True, blank=True)

    @property
    def cart_items_count(self):
        return CartProduct.objects.filter(cart__user=self, cart__status=1).aggregate(total=Sum('count'))['total'] or 0

    @property
    def wishlist_count(self):
        return WishList.objects.filter(user=self, product__isnull=False).count()

    class Meta(AbstractUser.Meta):
        swappable = "AUTH_USER_MODEL"




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
    (1, 'No Faol'),
    (2, "Yig'ilmoqda"),
    (3, "Yo'lda"),
    (4, "Yetkazilgan"),
    (5, "Qaytarilgan")
)

class Cart(Code):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.IntegerField(choices=CART_STATUS, default=1)
    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user} - {self.status}'

    @property
    def cart_products(self):
        return self.cartproduct_set.select_related('product')

    @property
    def total_price(self):
        return sum(item.total_price for item in self.cart_products)

    @property
    def count_product(self):
        return sum(item.count for item in self.cart_products)


class CartProduct(Code):
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE)
    count = models.IntegerField()

    @property
    def total_price(self):
        if not self.product:
            return 0
        return self.product.price * self.count


class WishList(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user} - {self.product}'


