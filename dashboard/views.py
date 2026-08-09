from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test, login_required
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from main import models
from django.contrib import messages
from openpyxl import Workbook
from django.http import HttpResponse


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def index(request):
    from django.db.models import Sum, F
    from django.db.models.functions import TruncMonth
    from django.utils.timezone import now

    # Get sales data
    sales = (
        models.CartProduct.objects
        .filter(cart__status=4)
        .annotate(month=TruncMonth('cart__date'))
        .values('month')
        .annotate(
            total=Sum(F('product__price') * F('count')),
            discount=Sum(
                (F('product__price') - F('product__discount_price')) * F('count')
            )
        )
        .order_by('month')
    )

    totals = [float(d['total'] or 0) for d in sales]

    stats = {
        'total_customers': models.User.objects.count(),
        'total_income': sum(totals),
        'completed_orders': models.Cart.objects.filter(status=4).count(),
        'new_customers': models.User.objects.filter(
            date_joined__month=now().month
        ).count(),
        'pending_orders': models.Cart.objects.filter(status=1).count(),
        'total_orders': models.Cart.objects.count(),
    }

    return render(request, 'dashboard/index.html', {'stats': stats})


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def create_category(request):
    if request.method == 'POST':
        try:
            name = request.POST.get('name')
            logo = request.FILES.get('logo')
            if not name or not logo:
                messages.warning(request, 'Barcha maydonlarni toldirish shart')
                return redirect('d_create_category')
            category = models.Category.objects.create(name=name, logo=logo)
            messages.success(request, 'Categorya yaratildi')
            return redirect('d_index')
        except Exception as e:
            messages.error(request, 'Xatolik')
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
    category = models.Category.objects.get(id=id)
    if request.method == 'POST':
        category.name = request.POST.get('name')
        status = request.POST.get('is_active')
        if status:
            category.is_active = True
        else:
            category.is_active = False

        logo = request.FILES.get('logo')
        if logo:
            category.logo = logo
        category.save()
        return redirect('d_list_category')

    context = {'category': category}
    return render(request, 'dashboard/edit_category.html', context=context)


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def delete_category(request, id):
    category = models.Category.objects.get(id=id)
    category.delete()
    return redirect('d_list_category')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def create_product(request):
    categories = models.Category.objects.all()
    print(f"Categories count: {categories.count()}")
    print(f"Categories: {list(categories.values('id', 'name'))}")
    if request.method == 'POST':
        try:
            name = request.POST.get('name')
            category_id = request.POST.get('category')
            description = request.POST.get('description')
            price = request.POST.get('price')
            discount_price = request.POST.get('discount_price')
            image = request.FILES.get('image')
            discount_status = request.POST.get('discount_status')
            count = request.POST.get('count')

            if not name or not category_id or not description or not price or not image:
                messages.warning(request, 'Barcha majburiy maydonlarni toldirish shart')
                return render(request, 'dashboard/create_praduct.html', {'categories': categories})

            category = models.Category.objects.get(id=category_id)
            product = models.Product.objects.create(
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
    product = models.Product.objects.get(code=code)
    categories = models.Category.objects.all()
    if request.method == 'POST':
        try:
            product.name = request.POST.get('name')
            category_id = request.POST.get('category')
            product.category = models.Category.objects.get(id=category_id)
            product.description = request.POST.get('description')
            product.price = request.POST.get('price')
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
    product = models.Product.objects.get(code=code)
    product.delete()
    return redirect('d_list_product')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def orders(request):
    query = request.GET.get('query')
    order = models.Cart.objects.select_related('user').all().order_by('-id')
    if query:
        order = order.filter(code__icontains=query)

    return render(request, 'dashboard/order_list.html', {'orders': order, "query": query})


# ______________________________EXEl Export________________________________
from django.utils import timezone  # <-- Vaqt mintaqasi bilan ishlash uchun import qilamiz


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def export_orders(request):
    orders = models.Cart.objects.all().order_by('id')
    wb = Workbook()
    ws = wb.active
    ws.title = "Buyurtmalar"
    ws.append(
        ["TR", "Code", "User", "Status", "Date", "Total price", "Discount", "Total after discount", "Count product"])

    n = 0
    for i in orders:
        n += 1

        # 1. Sanani o'zgaruvchiga olamiz
        order_date = i.date

        # 2. Agar sanada vaqt mintaqasi bo'lsa, uni Excel tushunadigan sodda ko'rinishga keltiramiz
        if order_date and timezone.is_aware(order_date):
            order_date = timezone.make_naive(order_date)

        # Calculate totals from CartProduct
        cart_products = models.CartProduct.objects.filter(cart=i)
        total_price = sum(cp.total_price for cp in cart_products)
        discount = sum((cp.product.discount_price or 0) * cp.count for cp in cart_products if cp.product)
        count_product = cart_products.count()

        ws.append([
            n,
            i.code,
            i.user.username,
            i.status,
            order_date,
            total_price,
            discount,
            total_price - discount,
            count_product
        ])

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="orders{request.user.username}.xlsx"'
    wb.save(response)
    return response


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def status_update(request, code):
    order = models.Cart.objects.get(code=code)
    if order.status <= 4:
        order.status = order.status + 1
        order.save()
        messages.success(request, 'Status o`zgartirildi')
        return redirect('d_orders')
    messages.error(request, 'Xatolik')
    return redirect('d_orders')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def reject_cart(request, code):
    order = models.Cart.objects.filter(code=code).first()
    if order.status > 1:
        order.status = order.status - 1
        order.save()
        messages.success(request, 'Qaytarildi')
        return redirect('d_orders')
    messages.error(request, 'Xatolik')
    return redirect('d_orders')


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def cart_detail(request, code):
    order = models.Cart.objects.get(code=code)
    cart_products = models.CartProduct.objects.filter(cart=order)

    context = {
        'order': order,
        'cart_products': cart_products,
    }
    return render(request, 'dashboard/orders_detail.html', context=context)


# --------------------------AUTH-----------------------------------
def log_in(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(username=username, password=password, is_staff=True)
        if user is not None and user.is_staff:
            login(request, user)
            return redirect('d_index')
        messages.error(request, 'Xatolik')
        return redirect('d_login')

    return render(request, 'dashboard/login.html')


@require_POST
def log_out(request):
    logout(request)
    return redirect('d_login')


from django.utils.timezone import now
# views.py
from django.db.models import Sum, F
from django.db.models.functions import TruncMonth
from django.http import JsonResponse


@user_passes_test(lambda u: u.is_superuser, login_url='d_login')
def revenue_chart_data(request):
    # Cart_total_price - yetkazilgan buyurtmalar
    sales = (
        models.CartProduct.objects
        .filter(cart__status=4)
        .annotate(month=TruncMonth('cart__date'))
        .values('month')
        .annotate(
            total=Sum(F('product__price') * F('count')),
            discount=Sum(
                (F('product__price') - F('product__discount_price')) * F('count')
            )
        )
        .order_by('month')
    )

    labels = [d['month'].strftime('%B') for d in sales]
    totals = [float(d['total'] or 0) for d in sales]
    discounts = [float(d['discount'] or 0) for d in sales]

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
