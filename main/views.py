import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse
from django.views.decorators.http import require_POST



from . import models
from django.contrib.auth import authenticate, login, logout

def redirect_back(request, fallback='index', **fallback_kwargs):
    next_url = request.META.get('HTTP_REFERER')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(fallback, **fallback_kwargs)


def get_active_cart(user):
    cart = models.Cart.objects.filter(user=user, status=1).order_by('id').first()
    if cart:
        return cart
    return models.Cart.objects.create(user=user, status=1)


def index(request):
    search_query = request.GET.get('q', '').strip()
    categories = models.Category.objects.filter()[:10]
    top_categories = models.Category.objects.filter(is_active=True)[:7]
    products = models.Product.objects.all()
    banners = list(models.Banner.objects.select_related('product_1').all()[:3])
    services = models.Service.objects.filter(is_active=True).all()[:4]
    featured_banner = banners[0] if banners else None

    if search_query:
        products = products.filter(name__icontains=search_query)

    context = {
        'categories': categories,
        'top_categories': top_categories,
        'products': products,
        'featured_banner': featured_banner,
        'services': services,
        'site_stats': {
            'customers': models.User.objects.count(),
            'products': models.Product.objects.count(),
        },
        'search_query': search_query,
    }
    if request.user.is_authenticated:
        wishlist_ids = models.WishList.objects.filter(user=request.user).values_list('product_id', flat=True)
        cart_ids = models.CartProduct.objects.filter(cart__user=request.user, cart__status=1).values_list('product_id', flat=True)
        context['wishlist_ids'] = wishlist_ids
        context['cart_ids'] = cart_ids
    else:
        context['wishlist_ids'] = []
        context['cart_ids'] = []


    return render(request, 'front/index.html', context=context)



def product_detail(request, code):
    product = get_object_or_404(models.Product, code=code)
    related_products = models.Product.objects.filter(category=product.category).exclude(code=code)

    context = {
        "product":product,
        "related_products":related_products,
        "cart_ids": [],
        "wishlist_ids": [],
        "cart_qty": 1,
        "is_in_wishlist": False,
    }
    if request.user.is_authenticated:
        wishlist_ids = models.WishList.objects.filter(user=request.user).values_list('product_id', flat=True)
        cart_ids = models.CartProduct.objects.filter(cart__user=request.user).values_list('product_id', flat=True)
        context['wishlist_ids'] = wishlist_ids
        context['cart_ids'] = cart_ids
        context['is_in_wishlist'] = product.id in wishlist_ids
        cart_product = models.CartProduct.objects.filter(cart__user=request.user, cart__status=1, product=product).first()
        if cart_product:
            context['cart_qty'] = cart_product.count

    return render(request, 'front/detail.html', context=context)


def category_filter(request, category_id):
    search_query = (request.GET.get('q') or request.GET.get('query') or '').strip()
    categories = models.Category.objects.all()
    top_categories = models.Category.objects.filter(is_active=True)[:7]
    active_category = get_object_or_404(models.Category, id=category_id)
    products = models.Product.objects.filter(category=active_category)
    query = request.GET.get('query')
    if query:
        products = products.filter(name__icontains=query)

    if search_query:
        products = products.filter(name__icontains=search_query)

    free_products = models.Product.objects.filter(discount_status=True)[:8]

    context = {
        'categories': categories,
        'top_categories': top_categories,
        'products': products,
        'active_category': active_category.id,
        'active_category_name': active_category.name,
        'total_products': products.count(),
        'search_query': search_query,
        'wishlist_ids': [],
        'cart_ids': [],
        'query': query,
    }

    if request.user.is_authenticated:
        context['wishlist_ids'] = models.WishList.objects.filter(user=request.user).values_list('product_id', flat=True)
        context['cart_ids'] = models.CartProduct.objects.filter(cart__user=request.user, cart__status=1).values_list('product_id', flat=True)

    return render(request, 'front/category_filter.html', context)


def all_products(request):
    search_query = (request.GET.get('q') or request.GET.get('query') or '').strip()
    categories = models.Category.objects.all()
    top_categories = models.Category.objects.filter(is_active=True)[:7]
    products = models.Product.objects.all()
    query = request.GET.get('query')
    if query:
        products = products.filter(name__icontains=query)

    if search_query:
        products = products.filter(name__icontains=search_query)

    free_products = models.Product.objects.filter(discount_status=True)[:8]

    context = {
        'categories': categories,
        'top_categories': top_categories,
        'products': products,
        'active_category': None,
        'active_category_name': None,
        'total_products': products.count(),
        'search_query': search_query,
        'wishlist_ids': [],
        'cart_ids': [],
        'query': query,
    }

    if request.user.is_authenticated:
        context['wishlist_ids'] = models.WishList.objects.filter(user=request.user).values_list('product_id', flat=True)
        context['cart_ids'] = models.CartProduct.objects.filter(cart__user=request.user, cart__status=1).values_list('product_id', flat=True)

    return render(request, 'front/category_filter.html', context)


# --------------------AUTH------------------------------
def register(request):
    if request.method =="POST":
        username = request.POST['username'].strip()
        phone = request.POST['phone'].strip()
        password = request.POST['password']
        confirm_password = request.POST['confirm_password']
        if password == confirm_password:
            if models.User.objects.filter(username=username).exists():
                return render(request, 'front/register.html', {'error': 'Bu foydalanuvchi nomi band'})
            models.User.objects.create_user(username=username, password=password, phone=phone)
            user = authenticate(username=username, password=password)
            login(request, user)
            return redirect('index')

        else:
            return render(request, 'front/register.html', {'error': 'Parollar mos kelmadi'})

    return  render(request, 'front/register.html')


def log_in(request):
    if request.method == "POST":
        username = request.POST['username']
        password = request.POST['password']
        next_url = request.POST.get('next') or 'index'
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            next_url = 'index'
        user = authenticate(username=username, password=password)
        if user:
            login(request, user)
            return redirect(next_url)
        return render(request, 'front/login.html', {
            'next': request.POST.get('next', ''),
            'error': "Foydalanuvchi nomi yoki parol noto'g'ri.",
        })
    return render(request, 'front/login.html', {'next': request.GET.get('next', '')})

@require_POST
def log_out(request):
    logout(request)
    return redirect('index')


@login_required(login_url='login')
def profile(request):
    if request.method == "POST":
        user = request.user
        user.username = request.POST.get('username')
        user.last_name = request.POST.get('last_name')
        user.first_name = request.POST.get('first_name')
        user.phone = request.POST.get('phone')
        user.address = request.POST.get('address')
        if request.FILES.get('photo'):
            user.photo = request.FILES.get('photo')

        user.save()

    context = {
        'show_dashboard_link': request.user.is_staff,
    }
    return render(request, 'front/profile.html', context)


@login_required(login_url='login')
@require_POST
def add_to_cart(request, product_code):
    product = get_object_or_404(models.Product, code=product_code)
    cart = get_active_cart(request.user)
    cart_product = models.CartProduct.objects.filter(cart=cart, product=product).first()

    quantity = 1
    if request.method == 'POST':
        try:
            quantity = int(request.POST.get('quantity', 1))
        except (ValueError, TypeError):
            quantity = 1
    quantity = max(1, quantity)
    if quantity > product.count:
        quantity = product.count

    if quantity <= 0:
        messages.error(request, 'Mahsulot omborda mavjud emas')
        return redirect_back(request, 'product_detail', code=product.code)

    if cart_product:
        cart_product.count = min(cart_product.count + quantity, product.count)
        cart_product.save()
    else:
        models.CartProduct.objects.create(cart=cart, product=product, count=quantity)

    return redirect_back(request, 'product_detail', code=product.code)


@login_required(login_url='login')
@require_POST
def remove_from_cart(request, product_code):
    product = get_object_or_404(models.Product, code=product_code)
    models.CartProduct.objects.filter(cart__user=request.user, cart__status=1, product=product).delete()
    return redirect_back(request, 'product_detail', code=product.code)


@login_required(login_url='login')
def update_cart_quantity(request, product_code):
    if request.method == 'POST':
        product = get_object_or_404(models.Product, code=product_code)
        cart = get_object_or_404(models.Cart, user=request.user, status=1)
        cart_product = get_object_or_404(models.CartProduct, cart=cart, product=product)
        
        try:
            if request.content_type == 'application/json':
                data = json.loads(request.body)
                quantity = int(data.get('quantity', 0))
            else:
                quantity = int(request.POST.get('quantity', 0))
            quantity = min(quantity, product.count)

            if quantity <= 0:
                cart_product.delete()
                if request.content_type == 'application/json':
                    return JsonResponse({'status': 'deleted'})
                return redirect('cart')
            else:
                cart_product.count = quantity
                cart_product.save()
                if request.content_type == 'application/json':
                    return JsonResponse({
                        'status': 'updated',
                        'total_price': float(cart_product.total_price),
                        'count': cart_product.count
                    })
                return redirect_back(request, 'product_detail', code=product.code)
        except (ValueError, TypeError):
            if request.content_type == 'application/json':
                return JsonResponse({'status': 'error'}, status=400)
            return redirect_back(request, 'product_detail', code=product.code)


@login_required(login_url='login')
@require_POST
def add_wishlist(request, product_code):
    product = get_object_or_404(models.Product, code=product_code)
    element = models.WishList.objects.filter(product=product, user=request.user)
    if not element:
        models.WishList.objects.create(product=product, user=request.user)
    return redirect_back(request)

@login_required(login_url='login')
@require_POST
def delete_wishlist(request, product_code):
    product = get_object_or_404(models.Product, code=product_code)
    element = models.WishList.objects.filter(product=product, user=request.user)
    if element:
        element.delete()
        return redirect_back(request)
    return redirect_back(request)


@login_required(login_url='login')
def wishlist(request):
    wishlist_products = models.WishList.objects.filter(user=request.user).filter(product__isnull=False)
    context = {
        "wishlist_products": wishlist_products
    }
    return render(request, 'front/wishlist.html', context=context)


@login_required(login_url='login')
def cart(request):
    cart_products = models.CartProduct.objects.filter(
        cart__user=request.user,
        cart__status=1
    ).select_related('product', 'cart').filter(product__isnull=False)
    context = {
        "cart_products": cart_products,
        "cart_total": sum(item.total_price for item in cart_products),
        "cart_count": sum(item.count for item in cart_products),
    }
    return render(request, 'front/cart.html', context=context)


@login_required(login_url='login')
@require_POST
def checkout(request):
    cart = models.Cart.objects.filter(user=request.user, status=1).first()
    if not cart:
        messages.error(request, "Faol savat topilmadi")
        return redirect('cart')

    cart_products = cart.cart_products.filter(product__isnull=False)
    if not cart_products.exists():
        messages.error(request, "Checkout uchun savat bo'sh")
        return redirect('cart')

    cart.status = 2
    cart.save(update_fields=['status'])
    messages.success(request, f"Buyurtma qabul qilindi. Buyurtma kodi: {cart.code}")
    return redirect('order_detail', code=cart.code)


@login_required(login_url='login')
def order_history(request):
    orders = models.Cart.objects.filter(
        user=request.user,
        status__gt=1,
    ).order_by('-date')
    return render(request, 'front/orders.html', {'orders': orders})


@login_required(login_url='login')
def order_detail(request, code):
    order = get_object_or_404(models.Cart, user=request.user, code=code, status__gt=1)
    cart_products = order.cart_products.filter(product__isnull=False)
    context = {
        'order': order,
        'cart_products': cart_products,
    }
    return render(request, 'front/order_detail.html', context=context)
