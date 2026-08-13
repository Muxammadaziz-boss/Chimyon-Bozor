from collections import defaultdict
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from openpyxl import Workbook

from main import models


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def index(request):
    delivered_products = models.CartProduct.objects.filter(
        cart__status=4,
        product__isnull=False,
    ).select_related('product', 'cart')
    totals = [float(item.total_price) for item in delivered_products]

    stats = {
        'total_customers': models.User.objects.count(),
        'total_income': sum(totals),
        'completed_orders': models.Cart.objects.filter(status=4).count(),
        'new_customers': models.User.objects.filter(
            date_joined__month=now().month
        ).count(),
        'pending_orders': models.Cart.objects.filter(status__in=(2, 3)).count(),
        'total_orders': models.Cart.objects.exclude(status=1).count(),
    }

    return render(request, 'dashboard/index.html', {'stats': stats})


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def create_category(request):
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            logo = request.FILES.get('logo')
            if not name or not logo:
                messages.warning(request, 'Barcha maydonlarni to\'ldirish shart')
                return redirect('d_create_category')
            models.Category.objects.create(name=name, logo=logo)
            messages.success(request, 'Kategoriya muvaffaqiyatli yaratildi')
            return redirect('d_index')
        except Exception as e:
            messages.error(request, f'Xatolik: {e}')
            return redirect('d_create_category')
    return render(request, 'dashboard/create_category.html')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def list_category(request):
    query = request.GET.get('query')
    categories = models.Category.objects.all().order_by('-id')
    if query:
        categories = categories.filter(name__icontains=query)
    return render(request, 'dashboard/category_list.html', {'categories': categories, 'query': query})


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def edit_category(request, id):
    category = get_object_or_404(models.Category, id=id)
    if request.method == 'POST':
        category.name = request.POST.get('name', category.name)
        status = request.POST.get('is_active')
        category.is_active = bool(status)

        logo = request.FILES.get('logo')
        if logo:
            category.logo = logo
        category.save()
        messages.success(request, 'Kategoriya yangilandi')
        return redirect('d_list_category')

    context = {'category': category}
    return render(request, 'dashboard/edit_category.html', context=context)


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def delete_category(request, id):
    category = get_object_or_404(models.Category, id=id)
    category.delete()
    messages.success(request, 'Kategoriya o\'chirildi')
    return redirect('d_list_category')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def create_product(request):
    categories = models.Category.objects.all()
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            category_id = request.POST.get('category')
            description = request.POST.get('description', '').strip()
            price = request.POST.get('price')
            discount_price = request.POST.get('discount_price')
            image = request.FILES.get('image')
            discount_status = request.POST.get('discount_status')
            count = request.POST.get('count')

            if not name or not category_id or not description or not price or not image:
                messages.warning(request, 'Barcha majburiy maydonlarni to\'ldirish shart')
                return render(request, 'dashboard/create_praduct.html', {'categories': categories})

            category = get_object_or_404(models.Category, id=category_id)
            models.Product.objects.create(
                name=name,
                category=category,
                description=description,
                price=price,
                discount_price=discount_price if discount_price else None,
                image=image,
                discount_status=bool(discount_status),
                count=int(count) if count else 0
            )
            messages.success(request, 'Mahsulot yaratildi')
            return redirect('d_index')
        except Exception as e:
            messages.error(request, f'Xatolik: {str(e)}')
            return render(request, 'dashboard/create_praduct.html', {'categories': categories})

    return render(request, 'dashboard/create_praduct.html', {'categories': categories})


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def list_product(request):
    query = request.GET.get('query')
    products = models.Product.objects.all().order_by('-created_at')
    if query:
        products = products.filter(name__icontains=query)
    return render(request, 'dashboard/praduct_list.html', {'products': products, 'query': query})


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def edit_product(request, code):
    product = get_object_or_404(models.Product, code=code)
    categories = models.Category.objects.all()
    if request.method == 'POST':
        try:
            product.name = request.POST.get('name', product.name)
            category_id = request.POST.get('category')
            if category_id:
                product.category = get_object_or_404(models.Category, id=category_id)
            product.description = request.POST.get('description', product.description)
            product.price = request.POST.get('price', product.price)
            discount_price = request.POST.get('discount_price')
            product.discount_price = discount_price if discount_price else None
            product.discount_status = bool(request.POST.get('discount_status'))
            product.count = int(request.POST.get('count', 0))
            image = request.FILES.get('image')
            if image:
                product.image = image
            product.save()
            messages.success(request, 'Mahsulot yangilandi')
            return redirect('d_list_product')
        except Exception as e:
            messages.error(request, f'Xatolik: {str(e)}')
    context = {'product': product, 'categories': categories}
    return render(request, 'dashboard/edit_praduct.html', context=context)


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def delete_product(request, code):
    product = get_object_or_404(models.Product, code=code)
    product.delete()
    messages.success(request, 'Mahsulot o\'chirildi')
    return redirect('d_list_product')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def orders(request):
    query = request.GET.get('query')
    order = models.Cart.objects.select_related('user').exclude(status=1).order_by('-id')
    if query:
        order = order.filter(code__icontains=query)

    return render(request, 'dashboard/order_list.html', {'orders': order, "query": query})


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def export_orders(request):
    orders_qs = models.Cart.objects.exclude(status=1).order_by('id')
    wb = Workbook()
    ws = wb.active
    ws.title = "Buyurtmalar"
    ws.append(
        ["TR", "Code", "User", "Status", "Date", "Total price", "Discount", "Total after discount", "Count product"])

    n = 0
    for i in orders_qs:
        n += 1
        order_date = i.date
        if order_date and timezone.is_aware(order_date):
            order_date = timezone.make_naive(order_date)

        cart_products = models.CartProduct.objects.filter(cart=i)
        total_price = sum(cp.total_price for cp in cart_products)
        original_total = sum((cp.product.price * cp.count) for cp in cart_products if cp.product)
        discount = original_total - total_price
        count_product = sum(cp.count for cp in cart_products)

        ws.append([
            n,
            i.code,
            i.user.username if i.user else 'Anonim',
            i.status,
            order_date,
            total_price,
            discount,
            total_price,
            count_product
        ])

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="orders_{request.user.username}.xlsx"'
    wb.save(response)
    return response


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def status_update(request, code):
    order = get_object_or_404(models.Cart, code=code)
    if order.status in (2, 3):
        order.status = order.status + 1
        order.save()
        messages.success(request, 'Status o\'zgartirildi')
        return redirect('d_orders')
    messages.error(request, 'Statusni o\'zgartirib bo\'lmaydi')
    return redirect('d_orders')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def reject_cart(request, code):
    order = get_object_or_404(models.Cart, code=code)
    if order.status in (2, 3, 4):
        order.status = 5
        order.save()
        messages.success(request, 'Buyurtma bekor qilindi (Qaytarildi)')
        return redirect('d_orders')
    messages.error(request, 'Ushbu buyurtmani bekor qilib bo\'lmaydi')
    return redirect('d_orders')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def cart_detail(request, code):
    order = get_object_or_404(models.Cart.objects.exclude(status=1), code=code)
    cart_products = models.CartProduct.objects.filter(cart=order).select_related('product')

    context = {
        'order': order,
        'cart_products': cart_products,
    }
    return render(request, 'dashboard/orders_detail.html', context=context)


def log_in(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(username=username, password=password)
        if user is not None and (user.is_staff or user.is_superuser):
            login(request, user)
            return redirect('d_index')
        messages.error(request, 'Foydalanuvchi nomi yoki parol noto\'g\'ri yoxud ruxsat yo\'q')
        return redirect('d_login')

    return render(request, 'dashboard/login.html')


@require_POST
def log_out(request):
    logout(request)
    return redirect('d_login')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
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
            monthly[month]['discount'] += float((item.product.price - item.product.active_price) * item.count)

    ordered_months = sorted(monthly.keys())
    labels = [month.strftime('%B') for month in ordered_months]
    totals = [monthly[month]['total'] for month in ordered_months]
    discounts = [monthly[month]['discount'] for month in ordered_months]

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
