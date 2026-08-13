from collections import defaultdict
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from openpyxl import Workbook

from main import models
from main.validators import validate_image_file


def staff_required(view_func):
    """
    Faqat tizim ma'murlari (is_staff yoki is_superuser) uchun ruxsat beruvchi dekorator.
    """
    def check_user(u):
        return u.is_authenticated and (u.is_staff or u.is_superuser)
    return user_passes_test(check_user, login_url='d_login')(view_func)


# ==========================================
# 1. DASHBOARD BOSH SAHIFA
# ==========================================

@staff_required
def index(request):
    delivered_products = models.CartProduct.objects.filter(
        cart__status=4,
        product__isnull=False,
    ).select_related('product', 'cart')
    total_income = sum(float(item.total_price) for item in delivered_products)

    total_orders_qs = models.Cart.objects.exclude(status=1)

    stats = {
        'total_products': models.Product.objects.count(),
        'total_categories': models.Category.objects.count(),
        'total_customers': models.User.objects.count(),
        'total_income': total_income,
        'total_orders': total_orders_qs.count(),
        'new_orders': models.Cart.objects.filter(status=2).count(),
        'processing_orders': models.Cart.objects.filter(status=3).count(),
        'completed_orders': models.Cart.objects.filter(status=4).count(),
        'cancelled_orders': models.Cart.objects.filter(status=5).count(),
        'new_customers': models.User.objects.filter(
            date_joined__month=now().month
        ).count(),
    }

    recent_orders = (
        models.Cart.objects.exclude(status=1)
        .select_related('user')
        .prefetch_related('cartproduct_set__product')
        .order_by('-date', '-id')[:8]
    )

    recent_users = models.User.objects.order_by('-date_joined')[:6]

    return render(request, 'dashboard/index.html', {
        'stats': stats,
        'recent_orders': recent_orders,
        'recent_users': recent_users,
    })


# ==========================================
# 2. KATEGORIYA BOSHQARUVI
# ==========================================

@staff_required
def create_category(request):
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            logo = request.FILES.get('logo')
            is_active = bool(request.POST.get('is_active', True))

            if not name or not logo:
                messages.warning(request, "Kategoriya nomi va logotipini kiritish majburiy.")
                return render(request, 'dashboard/create_category.html')

            validate_image_file(logo)
            models.Category.objects.create(name=name, logo=logo, is_active=is_active)
            messages.success(request, f"'{name}' kategoriyasi muvaffaqiyatli yaratildi.")
            return redirect('d_list_category')
        except Exception as e:
            messages.error(request, f"Xatolik yuz berdi: {e}")
            return render(request, 'dashboard/create_category.html')

    return render(request, 'dashboard/create_category.html')


@staff_required
def list_category(request):
    query = request.GET.get('query', '').strip()
    status_filter = request.GET.get('status')
    
    categories = models.Category.objects.annotate(
        product_count=Count('product')
    ).order_by('-id')

    if query:
        categories = categories.filter(name__icontains=query)
    
    if status_filter == 'active':
        categories = categories.filter(is_active=True)
    elif status_filter == 'inactive':
        categories = categories.filter(is_active=False)

    paginator = Paginator(categories, 12)
    page = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    return render(request, 'dashboard/category_list.html', {
        'categories': page_obj,
        'page_obj': page_obj,
        'query': query,
        'status_filter': status_filter,
    })


@staff_required
def edit_category(request, id):
    category = get_object_or_404(models.Category, id=id)
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            if not name:
                messages.warning(request, "Kategoriya nomi bo'sh bo'lishi mumkin emas.")
                return render(request, 'dashboard/edit_category.html', {'category': category})

            category.name = name
            category.is_active = bool(request.POST.get('is_active'))

            logo = request.FILES.get('logo')
            if logo:
                validate_image_file(logo)
                category.logo = logo

            category.save()
            messages.success(request, f"'{category.name}' kategoriyasi muvaffaqiyatli yangilandi.")
            return redirect('d_list_category')
        except Exception as e:
            messages.error(request, f"Xatolik yuz berdi: {e}")

    return render(request, 'dashboard/edit_category.html', {'category': category})


@staff_required
def delete_category(request, id):
    category = get_object_or_404(models.Category, id=id)
    product_count = category.product_set.count()
    category_name = category.name
    category.delete()
    if product_count > 0:
        messages.warning(request, f"'{category_name}' kategoriyasi va unga tegishli {product_count} ta mahsulot o'chirildi.")
    else:
        messages.success(request, f"'{category_name}' kategoriyasi o'chirildi.")
    return redirect('d_list_category')


# ==========================================
# 3. MAHSULOT BOSHQARUVI
# ==========================================

@staff_required
def create_product(request):
    categories = models.Category.objects.all().order_by('name')
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            category_id = request.POST.get('category')
            description = request.POST.get('description', '').strip()
            price = request.POST.get('price')
            discount_price = request.POST.get('discount_price')
            image = request.FILES.get('image')
            discount_status = bool(request.POST.get('discount_status'))
            count = request.POST.get('count', 0)

            if not name or not category_id or not description or not price or not image:
                messages.warning(request, "Barcha majburiy maydonlarni to'ldiring va rasm yuklang.")
                return render(request, 'dashboard/create_praduct.html', {'categories': categories})

            validate_image_file(image)
            category = get_object_or_404(models.Category, id=category_id)
            
            price_val = float(price)
            disc_price_val = float(discount_price) if discount_price else None

            if disc_price_val is not None and disc_price_val >= price_val:
                messages.warning(request, "Chegirma narxi asosiy narxdan kichik bo'lishi kerak.")
                return render(request, 'dashboard/create_praduct.html', {'categories': categories})

            models.Product.objects.create(
                name=name,
                category=category,
                description=description,
                price=price_val,
                discount_price=disc_price_val,
                image=image,
                discount_status=discount_status,
                count=int(count) if count else 0
            )
            messages.success(request, f"'{name}' mahsuloti muvaffaqiyatli yaratildi.")
            return redirect('d_list_product')
        except Exception as e:
            messages.error(request, f"Xatolik yuz berdi: {str(e)}")
            return render(request, 'dashboard/create_praduct.html', {'categories': categories})

    return render(request, 'dashboard/create_praduct.html', {'categories': categories})


@staff_required
def list_product(request):
    query = request.GET.get('query', '').strip()
    category_id = request.GET.get('category_id')
    discount_filter = request.GET.get('discount')

    products = models.Product.objects.select_related('category').all().order_by('-created_at')

    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(code__icontains=query) | Q(description__icontains=query)
        )

    if category_id:
        products = products.filter(category_id=category_id)

    if discount_filter == '1':
        products = products.filter(discount_status=True)

    paginator = Paginator(products, 15)
    page = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    categories = models.Category.objects.all().order_by('name')

    return render(request, 'dashboard/praduct_list.html', {
        'products': page_obj,
        'page_obj': page_obj,
        'query': query,
        'categories': categories,
        'selected_category': category_id,
        'selected_discount': discount_filter,
    })


@staff_required
def edit_product(request, code):
    product = get_object_or_404(models.Product, code=code)
    categories = models.Category.objects.all().order_by('name')

    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            category_id = request.POST.get('category')
            description = request.POST.get('description', '').strip()
            price = request.POST.get('price')
            discount_price = request.POST.get('discount_price')
            discount_status = bool(request.POST.get('discount_status'))
            count = request.POST.get('count', 0)

            if not name or not price:
                messages.warning(request, "Mahsulot nomi va narxi bo'sh bo'lishi mumkin emas.")
                return render(request, 'dashboard/edit_praduct.html', {'product': product, 'categories': categories})

            product.name = name
            if category_id:
                product.category = get_object_or_404(models.Category, id=category_id)

            product.description = description
            price_val = float(price)
            disc_price_val = float(discount_price) if discount_price else None

            if disc_price_val is not None and disc_price_val >= price_val:
                messages.warning(request, "Chegirma narxi asosiy narxdan kichik bo'lishi kerak.")
                return render(request, 'dashboard/edit_praduct.html', {'product': product, 'categories': categories})

            product.price = price_val
            product.discount_price = disc_price_val
            product.discount_status = discount_status
            product.count = int(count) if count else 0

            image = request.FILES.get('image')
            if image:
                validate_image_file(image)
                product.image = image

            product.save()
            messages.success(request, f"'{product.name}' mahsuloti muvaffaqiyatli yangilandi.")
            return redirect('d_list_product')
        except Exception as e:
            messages.error(request, f"Xatolik yuz berdi: {str(e)}")

    context = {'product': product, 'categories': categories}
    return render(request, 'dashboard/edit_praduct.html', context=context)


@staff_required
def delete_product(request, code):
    product = get_object_or_404(models.Product, code=code)
    prod_name = product.name
    product.delete()
    messages.success(request, f"'{prod_name}' mahsuloti o'chirildi.")
    return redirect('d_list_product')


# ==========================================
# 4. BUYURTMALAR BOSHQARUVI
# ==========================================

@staff_required
def orders(request):
    query = request.GET.get('query', '').strip()
    status_filter = request.GET.get('status')

    order_qs = (
        models.Cart.objects.exclude(status=1)
        .select_related('user')
        .prefetch_related('cartproduct_set__product')
        .order_by('-date', '-id')
    )

    if query:
        order_qs = order_qs.filter(
            Q(code__icontains=query) |
            Q(user__username__icontains=query) |
            Q(user__first_name__icontains=query) |
            Q(user__last_name__icontains=query) |
            Q(user__phone__icontains=query)
        )

    if status_filter and status_filter.isdigit():
        order_qs = order_qs.filter(status=int(status_filter))

    paginator = Paginator(order_qs, 15)
    page = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    return render(request, 'dashboard/order_list.html', {
        'orders': page_obj,
        'page_obj': page_obj,
        'query': query,
        'status_filter': status_filter,
    })


@staff_required
def status_update(request, code):
    order = get_object_or_404(models.Cart, code=code)
    target_status = request.POST.get('target_status') if request.method == 'POST' else request.GET.get('target_status')
    
    if target_status and target_status.isdigit():
        new_st = int(target_status)
        if new_st in (2, 3, 4, 5):
            order.status = new_st
            order.save()
            status_labels = {2: "Qabul qilindi (Yangi)", 3: "Yo'lda / Jarayonda", 4: "Yetkazilgan", 5: "Bekor qilingan"}
            messages.success(request, f"Buyurtma #{order.code[:8]} statusi '{status_labels.get(new_st)}' ga o'zgartirildi.")
            return redirect(request.META.get('HTTP_REFERER', 'd_orders'))

    # Fallback to next step
    if order.status in (2, 3):
        order.status = order.status + 1
        order.save()
        messages.success(request, f"Buyurtma #{order.code[:8]} keyingi bosqichga o'tkazildi.")
    else:
        messages.warning(request, "Ushbu buyurtma statusini avtomatik oshirib bo'lmaydi.")

    return redirect(request.META.get('HTTP_REFERER', 'd_orders'))


@staff_required
def reject_cart(request, code):
    order = get_object_or_404(models.Cart, code=code)
    if order.status in (2, 3, 4):
        order.status = 5
        order.save()
        messages.success(request, f"Buyurtma #{order.code[:8]} bekor qilindi (Qaytarildi).")
    else:
        messages.warning(request, "Ushbu buyurtmani bekor qilib bo'lmaydi.")
    return redirect(request.META.get('HTTP_REFERER', 'd_orders'))


@staff_required
def cart_detail(request, code):
    order = get_object_or_404(
        models.Cart.objects.select_related('user'),
        code=code
    )
    cart_products = models.CartProduct.objects.filter(cart=order).select_related('product', 'product__category')

    total_amount = sum(cp.total_price for cp in cart_products)
    total_count = sum(cp.count for cp in cart_products)

    context = {
        'order': order,
        'cart_products': cart_products,
        'total_amount': total_amount,
        'total_count': total_count,
    }
    return render(request, 'dashboard/orders_detail.html', context=context)


@staff_required
def export_orders(request):
    orders_qs = (
        models.Cart.objects.exclude(status=1)
        .select_related('user')
        .prefetch_related('cartproduct_set__product')
        .order_by('-date', 'id')
    )
    wb = Workbook()
    ws = wb.active
    ws.title = "Buyurtmalar"
    ws.append([
        "№",
        "Buyurtma ID",
        "Mijoz",
        "Telefon",
        "Status",
        "Sana",
        "Mahsulotlar soni",
        "Jami summa (so'm)",
        "Manzil"
    ])

    status_map = {
        1: "Faol savat",
        2: "Yangi (Qabul qilindi)",
        3: "Yo'lda / Yig'ilmoqda",
        4: "Yetkazilgan",
        5: "Bekor qilingan"
    }

    for idx, order in enumerate(orders_qs, 1):
        order_date = order.date
        if order_date and timezone.is_aware(order_date):
            order_date = timezone.make_naive(order_date).strftime('%d.%m.%Y %H:%M')

        cart_products = order.cartproduct_set.all()
        total_price = sum(cp.total_price for cp in cart_products)
        count_product = sum(cp.count for cp in cart_products)
        user_phone = order.user.phone if hasattr(order.user, 'phone') and order.user.phone else '—'
        user_address = order.user.address if hasattr(order.user, 'address') and order.user.address else '—'

        ws.append([
            idx,
            order.code,
            order.user.get_full_name() or order.user.username if order.user else 'Anonim',
            user_phone,
            status_map.get(order.status, str(order.status)),
            order_date,
            count_product,
            total_price,
            user_address
        ])

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = f"chimyon_bozor_orders_{now().strftime('%Y%m%d_%H%M')}.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


# ==========================================
# 5. FOYDALANUVCHILAR BOSHQARUVI (USER MANAGEMENT)
# ==========================================

@staff_required
def list_users(request):
    query = request.GET.get('query', '').strip()
    role_filter = request.GET.get('role')

    users_qs = models.User.objects.annotate(
        order_count=Count('cart', filter=~Q(cart__status=1))
    ).order_by('-date_joined')

    if query:
        users_qs = users_qs.filter(
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query)
        )

    if role_filter == 'staff':
        users_qs = users_qs.filter(is_staff=True)
    elif role_filter == 'customer':
        users_qs = users_qs.filter(is_staff=False)

    paginator = Paginator(users_qs, 15)
    page = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    return render(request, 'dashboard/user_list.html', {
        'users': page_obj,
        'page_obj': page_obj,
        'query': query,
        'role_filter': role_filter,
    })


@staff_required
def toggle_user_status(request, id):
    target_user = get_object_or_404(models.User, id=id)
    
    # Superadmin yoki o'z profilini o'chirishga yo'l qo'ymaslik
    if target_user == request.user:
        messages.error(request, "O'z hisobingiz holatini o'zgartira olmaysiz.")
        return redirect('d_list_users')

    if target_user.is_superuser and not request.user.is_superuser:
        messages.error(request, "Superadmin holatini faqat boshqa superadmin o'zgartira oladi.")
        return redirect('d_list_users')

    target_user.is_active = not target_user.is_active
    target_user.save()
    
    holat = "faollashtirildi" if target_user.is_active else "faolsizlantirildi"
    messages.success(request, f"Foydalanuvchi '{target_user.username}' muvaffaqiyatli {holat}.")
    return redirect('d_list_users')


# ==========================================
# 6. AUTHENTICATION & CHART APIS
# ==========================================

def log_in(request):
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        return redirect('d_index')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(username=username, password=password)
        if user is not None and (user.is_staff or user.is_superuser):
            login(request, user)
            messages.success(request, f"Xush kelibsiz, {user.username}!")
            next_url = request.GET.get('next') or 'd_index'
            return redirect(next_url)
        messages.error(request, "Foydalanuvchi nomi yoki parol noto'g'ri yoxud admin ruxsati yo'q.")
        return redirect('d_login')

    return render(request, 'dashboard/login.html')


@require_POST
def log_out(request):
    logout(request)
    messages.info(request, "Tizimdan muvaffaqiyatli chiqdingiz.")
    return redirect('d_login')


@staff_required
def revenue_chart_data(request):
    monthly = defaultdict(lambda: {'total': 0.0, 'discount': 0.0})
    sales = models.CartProduct.objects.filter(
        cart__status=4,
        product__isnull=False,
    ).select_related('product', 'cart')

    for item in sales:
        if item.cart and item.cart.date:
            month = item.cart.date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            monthly[month]['total'] += float(item.total_price)
            if item.product and item.product.price:
                monthly[month]['discount'] += float((item.product.price - item.product.active_price) * item.count)

    ordered_months = sorted(monthly.keys())
    month_names_uz = {
        1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel", 5: "May", 6: "Iyun",
        7: "Iyul", 8: "Avgust", 9: "Sentyabr", 10: "Oktyabr", 11: "Noyabr", 12: "Dekabr"
    }
    labels = [month_names_uz.get(m.month, m.strftime('%B')) for m in ordered_months]
    totals = [monthly[m]['total'] for m in ordered_months]
    discounts = [monthly[m]['discount'] for m in ordered_months]

    stats = {
        'total_customers': models.User.objects.count(),
        'total_income': sum(totals),
        'completed_orders': models.Cart.objects.filter(status=4).count(),
        'new_customers': models.User.objects.filter(
            date_joined__month=now().month
        ).count(),
    }

    return JsonResponse({'labels': labels, 'totals': totals,
                         'discounts': discounts, 'stats': stats})
