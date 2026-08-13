from django.contrib import admin

from . import models


class CartProductInline(admin.TabularInline):
    model = models.CartProduct
    extra = 0


@admin.register(models.Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'id')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(models.Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'price', 'discount_price', 'count', 'discount_status')
    list_filter = ('category', 'discount_status')
    search_fields = ('name', 'code')


@admin.register(models.Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'duration', 'is_active')
    list_filter = ('is_active',)


@admin.register(models.Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ('title', 'product_1')


@admin.register(models.User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('username', 'phone', 'phone_verified', 'is_active', 'is_staff', 'is_superuser')
    list_filter = ('phone_verified', 'is_active', 'is_staff')
    search_fields = ('username', 'phone', 'email')


@admin.register(models.Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('code', 'user', 'status', 'date')
    list_filter = ('status',)
    inlines = [CartProductInline]


@admin.register(models.CartProduct)
class CartProductAdmin(admin.ModelAdmin):
    list_display = ('code', 'product', 'cart', 'count')


@admin.register(models.WishList)
class WishListAdmin(admin.ModelAdmin):
    list_display = ('user', 'product', 'date')


@admin.register(models.SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ('site_name', 'phone', 'email')


@admin.register(models.Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(models.OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone', 'code', 'created_at', 'is_used')
    list_filter = ('is_used', 'created_at')
    search_fields = ('phone', 'code', 'user__username')
