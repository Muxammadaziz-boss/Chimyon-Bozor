import json
import re
import random
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Count, Q
from django.shortcuts import get_object_or_404, render, redirect
from django.http import JsonResponse, HttpResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import models
from .utils import paginate_queryset
from .sms_service import send_sms_code
from .validators import validate_image_file
from .services.payment import PaymentManager
from .services.payment.base import PaymentConfigurationError
from django.contrib.auth import authenticate, login, logout

logger = logging.getLogger(__name__)
PHONE_RE = re.compile(r'^\+?998[0-9]{9}$')
send_otp_sms = send_sms_code


def redirect_back(request, fallback='index', **fallback_kwargs):
    referer = request.META.get('HTTP_REFERER')
    if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}):
        return redirect(referer)
    return redirect(fallback, **fallback_kwargs)


def get_active_cart(user):
    cart = models.Cart.objects.filter(user=user, status=1).order_by('id').first()
    if cart:
        return cart
    return models.Cart.objects.create(user=user, status=1)


def _attach_cart_wishlist_context(request, context):
    cart_ids = []
    wishlist_ids = []
    if request.user.is_authenticated:
        cart = models.Cart.objects.filter(user=request.user, status=1).first()
        if cart:
            cart_ids = list(models.CartProduct.objects.filter(cart=cart).values_list('product_id', flat=True))
        wishlist_ids = list(models.WishList.objects.filter(user=request.user).values_list('product_id', flat=True))
    context['cart_ids'] = cart_ids
    context['wishlist_ids'] = wishlist_ids
    return context


def index(request):
    search_query = request.GET.get('q', '').strip()
    categories = models.Category.objects.filter(is_active=True)[:10]
    top_categories = models.Category.objects.filter(is_active=True)[:7]
    top_4_categories = list(models.Category.objects.filter(is_active=True).annotate(prod_count=Count('product')).order_by('-prod_count')[:4])
    banners = list(models.Banner.objects.select_related('product_1').all()[:3])
    services = models.Service.objects.filter(is_active=True).all()[:4]
    featured_banner = banners[0] if banners else None

    # Fetch active categories that have products for random home page sections
    active_categories = list(
        models.Category.objects.filter(is_active=True)
        .annotate(prod_count=Count('product'))
        .filter(prod_count__gt=0)
        .order_by('?')
    )

    cat_section_1 = None
    cat_section_1_products = []
    cat_section_2 = None
    cat_section_2_products = []

    if len(active_categories) > 0:
        cat_section_1 = active_categories[0]
        cat_section_1_products = list(
            models.Product.objects.filter(category=cat_section_1).order_by('-created_at')[:5]
        )

    if len(active_categories) > 1:
        cat_section_2 = active_categories[1]
        cat_section_2_products = list(
            models.Product.objects.filter(category=cat_section_2).order_by('-created_at')[:5]
        )
    elif cat_section_1:
        cat_section_2 = cat_section_1
        cat_section_2_products = list(
            models.Product.objects.filter(category=cat_section_2).order_by('created_at')[:5]
        )

    # Chegirmalı mahsulotlar
    discounted_products = list(
        models.Product.objects.filter(discount_status=True, discount_price__isnull=False)
        .order_by('-created_at')[:10]
    )
    # Yangi mahsulotlar
    new_products = list(
        models.Product.objects.order_by('-created_at')[:10]
    )

    context = {
        'categories': categories,
        'top_categories': top_categories,
        'top_4_categories': top_4_categories,
        'cat_section_1': cat_section_1,
        'cat_section_1_products': cat_section_1_products,
        'cat_section_2': cat_section_2,
        'cat_section_2_products': cat_section_2_products,
        'banners': banners,
        'featured_banner': featured_banner,
        'services': services,
        'discounted_products': discounted_products,
        'new_products': new_products,
        'site_stats': {
            'customers': models.User.objects.count(),
            'products': models.Product.objects.count(),
        },
        'search_query': search_query,
    }
    _attach_cart_wishlist_context(request, context)
    return render(request, 'front/index.html', context=context)


def product_detail(request, code):
    product = get_object_or_404(models.Product, code=code)
    
    related_base_qs = models.Product.objects.filter(category=product.category).exclude(code=code)
    related_products_count = related_base_qs.count()
    related_products = related_base_qs[:10]
    
    similar_category = models.Category.objects.exclude(id=product.category.id).order_by('?').first()
    similar_category_products = models.Product.objects.filter(category=similar_category)[:10] if similar_category else []

    context = {
        "product": product,
        "related_products": related_products,
        "related_products_count": related_products_count,
        "similar_category": similar_category,
        "similar_category_products": similar_category_products,
        "cart_ids": [],
        "wishlist_ids": [],
        "cart_qty": 1,
        "is_in_wishlist": False,
    }
    if request.user.is_authenticated:
        wishlist_ids = list(models.WishList.objects.filter(
            user=request.user
        ).values_list('product_id', flat=True))
        cart_ids = list(models.CartProduct.objects.filter(
            cart__user=request.user, cart__status=1
        ).values_list('product_id', flat=True))
        context['wishlist_ids'] = wishlist_ids
        context['cart_ids'] = cart_ids
        context['is_in_wishlist'] = product.id in wishlist_ids
        cart_product = models.CartProduct.objects.filter(
            cart__user=request.user, cart__status=1, product=product
        ).first()
        if cart_product:
            context['cart_qty'] = cart_product.count

    return render(request, 'front/detail.html', context=context)


def load_more_related_products(request, code):
    from django.template.loader import render_to_string
    offset = int(request.GET.get('offset', 10))
    limit = 10
    product = get_object_or_404(models.Product, code=code)
    
    related_products = models.Product.objects.filter(category=product.category).exclude(code=code)[offset:offset+limit]
    
    html = ''
    for p in related_products:
        html += '<div class="col">' + render_to_string('front/partials/product_card.html', {'product': p, 'request': request}) + '</div>'
        
    return JsonResponse({'html': html, 'count': related_products.count()})


@login_required(login_url='login')
def add_review(request, product_code):
    if request.method == 'POST':
        product = get_object_or_404(models.Product, code=product_code)
        rating = int(request.POST.get('rating', 5))
        text = request.POST.get('text', '').strip()
        if text:
            models.Review.objects.create(
                user=request.user,
                product=product,
                rating=rating,
                text=text
            )
        return redirect('product_detail', code=product_code)
    return redirect('index')


def _build_catalog_context(request, products_qs, active_category=None):
    search_query = (request.GET.get('q') or request.GET.get('query') or request.GET.get('search') or '').strip()
    query = request.GET.get('query')
    if query:
        products_qs = products_qs.filter(name__icontains=query)
    if search_query:
        from django.db.models import Q
        products_qs = products_qs.filter(Q(name__icontains=search_query) | Q(category__name__icontains=search_query) | Q(code__icontains=search_query))

    products_qs = products_qs.select_related('category').order_by('-created_at')
    page_obj = paginate_queryset(request, products_qs, per_page=20)

    context = {
        'categories': models.Category.objects.all(),
        'top_categories': models.Category.objects.filter(is_active=True)[:7],
        'products': page_obj.object_list,
        'page_obj': page_obj,
        'active_category': active_category.id if active_category else None,
        'active_category_name': active_category.name if active_category else None,
        'total_products': products_qs.count(),
        'search_query': search_query,
        'query': query,
    }
    _attach_cart_wishlist_context(request, context)
    return context


def category_filter(request, category_id):
    active_category = get_object_or_404(models.Category, id=category_id)
    products_qs = models.Product.objects.filter(category=active_category)
    context = _build_catalog_context(request, products_qs, active_category=active_category)
    return render(request, 'front/category_filter.html', context)


def all_products(request):
    products_qs = models.Product.objects.all()
    context = _build_catalog_context(request, products_qs)
    return render(request, 'front/category_filter.html', context)


def register(request):
    if request.method == "POST":
        username = request.POST.get('username', '').strip()
        raw_phone = request.POST.get('phone', '').strip().replace(' ', '').replace('-', '').replace('+', '')
        password = request.POST.get('password', '')
        confirm_password = request.POST.get('confirm_password', '')

        # Normalize phone: ensure +998 prefix
        if raw_phone.startswith('998'):
            phone = '+' + raw_phone
        else:
            phone = '+998' + raw_phone

        reg_data = {
            'is_register_page': True,
            'reg_username': username,
            'reg_phone': request.POST.get('phone', '')
        }

        if len(username) < 4:
            reg_data['reg_error'] = "Foydalanuvchi nomi kamida 4 ta belgidan iborat bo'lishi kerak."
            return render(request, 'front/login.html', reg_data)

        if password != confirm_password:
            reg_data['reg_error'] = 'Parollar mos kelmadi'
            return render(request, 'front/login.html', reg_data)

        if not PHONE_RE.match(phone):
            reg_data['reg_error'] = "Telefon raqam noto'g'ri formatda. 9 ta raqam kiriting (masalan: 917914881)."
            return render(request, 'front/login.html', reg_data)

        if models.User.objects.filter(username__iexact=username).exists():
            reg_data['reg_error'] = "Ushbu foydalanuvchi nomi allaqachon band. Boshqa nom tanlang."
            return render(request, 'front/login.html', reg_data)
        if models.User.objects.filter(phone=phone).exists():
            reg_data['reg_error'] = "Ushbu telefon raqami allaqachon ro'yxatdan o'tgan."
            return render(request, 'front/login.html', reg_data)

        # Create unverified inactive user pending OTP confirmation
        user = models.User.objects.create_user(
            username=username,
            password=password,
            phone=phone,
            is_active=False,
            phone_verified=False
        )

        # Generate 6-digit OTP code
        otp_code = str(random.randint(100000, 999999))
        
        # Save OTP to database
        models.OTPCode.objects.create(
            user=user,
            phone=phone,
            code=otp_code
        )

        # Dispatch OTP via SMS queue
        send_otp_sms(phone, otp_code)
        # Store pending user ID in session
        request.session['otp_user_id'] = user.id
        request.session['otp_phone'] = phone

        messages.info(request, f"Telefoningizga ({phone}) 6 xonali SMS kod yuborildi. Kodni kiriting.")
        return redirect('verify_otp')


def verify_otp(request):
    user_id = request.session.get('otp_user_id')
    phone = request.session.get('otp_phone', '')
    
    if not user_id:
        messages.error(request, "SMS tasdiqlash seans topshiriqlari topilmadi. Qayta ro'yxatdan o'ting.")
        return redirect('login')

    user = models.User.objects.filter(pk=user_id).first()
    if not user:
        messages.error(request, "Foydalanuvchi topilmadi.")
        return redirect('login')

    if request.method == "POST":
        entered_code = request.POST.get('otp_code', '').strip().replace(' ', '')
        
        # Fetch latest valid OTP for user
        otp_obj = models.OTPCode.objects.filter(user=user, is_used=False).order_by('-created_at').first()
        
        if not otp_obj:
            return render(request, 'front/verify_otp.html', {
                'phone': phone,
                'error': "SMS kodi topilmadi yoki foydalanib bo'lingan."
            })

        if not otp_obj.is_valid():
            return render(request, 'front/verify_otp.html', {
                'phone': phone,
                'error': "SMS kodining amal qilish muddati tugagan (5 minut). Kodni qayta yuboring."
            })

        # Rate limit attempts (Max 5 attempts)
        attempts = request.session.get('otp_attempts', 0) + 1
        request.session['otp_attempts'] = attempts

        if attempts > 5:
            models.OTPCode.objects.filter(user=user, is_used=False).update(is_used=True)
            request.session.pop('otp_attempts', None)
            return render(request, 'front/verify_otp.html', {
                'phone': phone,
                'error': "5 marta noto'g'ri kod kiritildi. Xavfsizlik yuzasidan kod bekor qilindi. Iltimos, 'Kodni qayta yuborish' tugmasini bosing."
            })

        if otp_obj.code == entered_code:
            # Mark OTP used
            otp_obj.is_used = True
            otp_obj.save()

            # Activate user account and set phone_verified=True
            user.phone_verified = True
            user.is_active = True
            user.save()

            # Clean session
            request.session.pop('otp_user_id', None)
            request.session.pop('otp_phone', None)
            request.session.pop('otp_attempts', None)

            # Log user in
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, "Telefon raqamingiz muvaffaqiyatli tasdiqlandi va akkauntingiz faollashtirildi! 🎉")
            return redirect('index')
        else:
            remaining = max(0, 5 - attempts)
            return render(request, 'front/verify_otp.html', {
                'phone': phone,
                'error': f"Kiritilgan SMS kod noto'g'ri. Qolgan urinishlar: {remaining}"
            })

    latest_otp = models.OTPCode.objects.filter(user=user, is_used=False).order_by('-created_at').first()
    demo_code = latest_otp.code if latest_otp else None

    return render(request, 'front/verify_otp.html', {
        'phone': phone,
        'demo_code': demo_code
    })


def resend_otp(request):
    user_id = request.session.get('otp_user_id')
    phone = request.session.get('otp_phone', '')
    
    if not user_id:
        messages.error(request, "Seans eskirgan.")
        return redirect('login')

    user = models.User.objects.filter(pk=user_id).first()
    if user:
        # Invalidate old OTPs
        models.OTPCode.objects.filter(user=user, is_used=False).update(is_used=True)

        # Create new 6-digit OTP
        new_code = str(random.randint(100000, 999999))
        models.OTPCode.objects.create(
            user=user,
            phone=phone,
            code=new_code
        )

        send_otp_sms(phone, new_code)
        messages.success(request, f"Yangi SMS kod {phone} raqamiga yuborildi.")

    return redirect('verify_otp')


def log_in(request):
    if request.method == "POST":
        username = request.POST['username']
        password = request.POST['password']
        next_url = request.POST.get('next') or 'index'
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            next_url = 'index'

        # Check if user exists but unverified
        existing_user = models.User.objects.filter(username__iexact=username).first()
        if existing_user and existing_user.check_password(password):
            if not existing_user.phone_verified or not existing_user.is_active:
                request.session['otp_user_id'] = existing_user.id
                request.session['otp_phone'] = existing_user.phone or ''
                
                # Generate fresh OTP
                otp_code = str(random.randint(100000, 999999))
                models.OTPCode.objects.filter(user=existing_user, is_used=False).update(is_used=True)
                models.OTPCode.objects.create(user=existing_user, phone=existing_user.phone or '', code=otp_code)
                send_otp_sms(existing_user.phone, otp_code)

                messages.warning(request, "Telefon raqamingiz hali tasdiqlanmagan. SMS kodingiz yuborildi.")
                return redirect('verify_otp')

        user = authenticate(username=username, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, f"Xush kelibsiz, {user.username}!")
            return redirect(next_url)
        return render(request, 'front/login.html', {
            'next': request.POST.get('next', ''),
            'error': "Foydalanuvchi nomi yoki parol noto'g'ri.",
        })
    return render(request, 'front/login.html', {'next': request.GET.get('next', '')})


@require_POST
def log_out(request):
    logout(request)
    messages.success(request, 'Tizimdan chiqdingiz')
    return redirect('index')


@login_required(login_url='login')
def profile(request):
    if request.method == "POST":
        user = request.user
        new_username = (request.POST.get('username') or '').strip()
        if new_username and new_username != user.username:
            if models.User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                messages.error(request, 'Bu foydalanuvchi nomi band')
                return redirect('profile')
            user.username = new_username

        user.last_name = request.POST.get('last_name', '')
        user.first_name = request.POST.get('first_name', '')
        user.phone = request.POST.get('phone', '')
        user.address = request.POST.get('address', '')
        if request.FILES.get('photo'):
            photo_file = request.FILES.get('photo')
            try:
                validate_image_file(photo_file)
                user.photo = photo_file
            except Exception as e:
                messages.error(request, f"Rasm xatosi: {e}")
                return redirect('profile')
        user.save()
        messages.success(request, "Ma'lumotlar muvaffaqiyatli saqlandi")

    orders = models.Cart.objects.filter(
        user=request.user,
        status__gt=1,
    ).order_by('-date')

    addresses = models.Address.objects.filter(is_active=True).order_by('name')

    context = {
        'show_dashboard_link': request.user.is_staff,
        'orders': orders,
        'addresses': addresses,
    }
    return render(request, 'front/profile.html', context)


@login_required(login_url='login')
@require_POST
def add_to_cart(request, product_code):
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    if not request.user.is_authenticated:
        if is_ajax:
            return JsonResponse({'status': 'login_required', 'message': 'Iltimos, avval tizimga kiring', 'redirect': '/login/'}, status=401)
        return redirect('login')

    product = get_object_or_404(models.Product, code=product_code)
    cart = get_active_cart(request.user)
    cart_product = models.CartProduct.objects.filter(cart=cart, product=product).first()

    try:
        quantity = int(request.POST.get('quantity', 1))
    except (ValueError, TypeError):
        quantity = 1
    quantity = max(1, quantity)

    if product.count <= 0:
        if is_ajax:
            return JsonResponse({'status': 'error', 'message': 'Mahsulot omborda mavjud emas'}, status=400)
        messages.error(request, 'Mahsulot omborda mavjud emas')
        return redirect_back(request, 'product_detail', code=product.code)

    if quantity > product.count:
        quantity = product.count

    if cart_product:
        cart_product.count = min(cart_product.count + quantity, product.count)
        cart_product.save()
    else:
        models.CartProduct.objects.create(cart=cart, product=product, count=quantity)

    if is_ajax:
        return JsonResponse({
            'status': 'success',
            'message': f'"{product.name}" savatga qo\'shildi',
            'cart_count': request.user.cart_items_count,
            'in_cart': True
        })
    messages.success(request, f'"{product.name}" savatga qo\'shildi')
    return redirect_back(request, 'product_detail', code=product.code)


@require_POST
def remove_from_cart(request, product_code):
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    if not request.user.is_authenticated:
        if is_ajax:
            return JsonResponse({'status': 'login_required', 'message': 'Iltimos, avval tizimga kiring', 'redirect': '/login/'}, status=401)
        return redirect('login')

    product = get_object_or_404(models.Product, code=product_code)
    models.CartProduct.objects.filter(
        cart__user=request.user, cart__status=1, product=product
    ).delete()
    if is_ajax:
        return JsonResponse({
            'status': 'success',
            'message': f'"{product.name}" savatdan olib tashlandi',
            'cart_count': request.user.cart_items_count,
            'in_cart': False
        })
    messages.success(request, f'"{product.name}" savatdan olib tashlandi')
    return redirect_back(request, 'product_detail', code=product.code)


@require_POST
def update_cart_quantity(request, product_code):
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'login_required', 'redirect': '/login/'}, status=401)
    product = get_object_or_404(models.Product, code=product_code)
    cart = get_object_or_404(models.Cart, user=request.user, status=1)
    cart_product = get_object_or_404(models.CartProduct, cart=cart, product=product)

    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body)
            quantity = int(data.get('quantity', 0))
        else:
            quantity = int(request.POST.get('quantity', 0))

        stock_warning = None
        if quantity > product.count:
            quantity = product.count
            stock_warning = f"Omborda faqat {product.count} ta mahsulot mavjud"

        if quantity <= 0:
            cart_product.delete()
            if request.content_type == 'application/json':
                return JsonResponse({
                    'status': 'deleted',
                    'cart_total': float(cart.total_price),
                    'cart_count': cart.count_product,
                    'cart_items_count': cart.cart_products.count(),
                })
            messages.success(request, f'"{product.name}" savatdan olib tashlandi')
            return redirect('cart')

        cart_product.count = quantity
        cart_product.save()

        if request.content_type == 'application/json':
            return JsonResponse({
                'status': 'updated',
                'item_total_price': float(cart_product.total_price),
                'count': cart_product.count,
                'max_stock': product.count,
                'stock_warning': stock_warning,
                'cart_total': float(cart.total_price),
                'cart_count': cart.count_product,
                'cart_items_count': cart.cart_products.count(),
            })
        messages.success(request, 'Savat yangilandi')
        return redirect_back(request, 'product_detail', code=product.code)
    except (ValueError, TypeError, json.JSONDecodeError):
        if request.content_type == 'application/json':
            return JsonResponse({'status': 'error', 'message': "Noto'g'ri qiymat kiritildi"}, status=400)
        return redirect_back(request, 'product_detail', code=product.code)


@require_POST
def add_wishlist(request, product_code):
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    if not request.user.is_authenticated:
        if is_ajax:
            return JsonResponse({'status': 'login_required', 'message': 'Iltimos, avval tizimga kiring', 'redirect': '/login/'}, status=401)
        return redirect('login')

    product = get_object_or_404(models.Product, code=product_code)
    if not models.WishList.objects.filter(product=product, user=request.user).exists():
        models.WishList.objects.create(product=product, user=request.user)
        if not is_ajax:
            messages.success(request, f'"{product.name}" sevimlilarga qo\'shildi')
    if is_ajax:
        return JsonResponse({
            'status': 'success',
            'message': f'"{product.name}" sevimlilarga qo\'shildi',
            'wishlist_count': request.user.wishlist_count,
            'in_wishlist': True
        })
    return redirect_back(request)


@require_POST
def delete_wishlist(request, product_code):
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1'
    if not request.user.is_authenticated:
        if is_ajax:
            return JsonResponse({'status': 'login_required', 'message': 'Iltimos, avval tizimga kiring', 'redirect': '/login/'}, status=401)
        return redirect('login')

    product = get_object_or_404(models.Product, code=product_code)
    models.WishList.objects.filter(product=product, user=request.user).delete()
    if not is_ajax:
        messages.success(request, f'"{product.name}" sevimlilardan olib tashlandi')
    if is_ajax:
        return JsonResponse({
            'status': 'success',
            'message': f'"{product.name}" sevimlilardan olib tashlandi',
            'wishlist_count': request.user.wishlist_count,
            'in_wishlist': False
        })
    return redirect_back(request)


@login_required(login_url='login')
def wishlist(request):
    wishlist_products = models.WishList.objects.filter(
        user=request.user, product__isnull=False
    ).select_related('product', 'product__category')
    return render(request, 'front/wishlist.html', {
        "wishlist_products": wishlist_products
    })


@login_required(login_url='login')
def cart(request):
    cart_products = models.CartProduct.objects.filter(
        cart__user=request.user,
        cart__status=1,
        product__isnull=False,
    ).select_related('product', 'product__category', 'cart')
    context = {
        "cart_products": cart_products,
        "cart_total": sum(item.total_price for item in cart_products),
        "cart_count": sum(item.count for item in cart_products),
    }
    return render(request, 'front/cart.html', context=context)


@login_required(login_url='login')
def checkout(request):
    """
    Checkout sahifasi (GET) va Buyurtma/To'lov yaratish (POST).
    Partial Prepayment (0%, 30%, 50%, 100%) tizimi bilan to'liq integratsiya qilingan.
    Telefon raqami qat'iy tekshiriladi va manzil admin qo'shgan manzillardan tanlanadi.
    """
    user = request.user
    cart = models.Cart.objects.filter(user=user, status=1).first()
    if not cart or not cart.cart_products.filter(product__isnull=False).exists():
        messages.error(request, "Savatingiz bo'sh. Iltimos, avval mahsulot tanlang.")
        return redirect('cart')

    cart_products = list(cart.cart_products.filter(product__isnull=False).select_related('product', 'product__category'))
    cart_count = sum(item.count for item in cart_products)
    financials = PaymentManager.calculate_order_financials(cart)
    addresses = models.Address.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        raw_phone = request.POST.get('phone', '').strip()
        if not raw_phone and user.phone:
            raw_phone = str(user.phone).strip()

        address = request.POST.get('address', '').strip()
        if not address and user.address:
            address = str(user.address).strip()

        prepayment_percent_raw = request.POST.get('prepayment_percent')
        chosen_percent = None

        # Check if phone contains illegal characters (letters, etc.)
        if not raw_phone or not re.match(r'^\+?[0-9\s\-()]+$', raw_phone):
            messages.error(request, "Telefon raqami noto'g'ri kiritildi. Iltimos, to'g'ri O'zbekiston raqamini kiriting (masalan: +998 90 123 45 67).")
            return render(request, 'front/checkout.html', {
                'cart': cart,
                'cart_products': cart_products,
                'cart_total': financials['grand_total'],
                'cart_count': cart_count,
                'financials': financials,
                'addresses': addresses,
                'selected_provider': request.POST.get('provider', 'click').strip().lower(),
                'input_phone': raw_phone,
                'selected_address': address,
            })

        # Clean and normalize phone
        clean_phone_digits = re.sub(r'[^\d]', '', raw_phone)
        if clean_phone_digits.startswith('998') and len(clean_phone_digits) == 12:
            phone = '+' + clean_phone_digits
        elif len(clean_phone_digits) == 9:
            phone = '+998' + clean_phone_digits
        else:
            phone = '+' + clean_phone_digits if clean_phone_digits else ''

        if not phone or not PHONE_RE.match(phone):
            messages.error(request, "Telefon raqami noto'g'ri kiritildi. Iltimos, to'g'ri O'zbekiston raqamini kiriting (masalan: +998 90 123 45 67).")
            return render(request, 'front/checkout.html', {
                'cart': cart,
                'cart_products': cart_products,
                'cart_total': financials['grand_total'],
                'cart_count': cart_count,
                'financials': financials,
                'addresses': addresses,
                'selected_provider': request.POST.get('provider', 'click').strip().lower(),
                'input_phone': raw_phone,
                'selected_address': address,
            })

        # Validate Address
        if not address:
            messages.error(request, "Iltimos, yetkazib berish manzilini tanlang.")
            return render(request, 'front/checkout.html', {
                'cart': cart,
                'cart_products': cart_products,
                'cart_total': financials['grand_total'],
                'cart_count': cart_count,
                'financials': financials,
                'addresses': addresses,
                'selected_provider': request.POST.get('provider', 'click').strip().lower(),
                'input_phone': phone,
                'selected_address': address,
            })

        if addresses.exists() and not addresses.filter(name=address).exists():
            messages.error(request, "Iltimos, admin tomonidan qo'shilgan tasdiqlangan manzillardan birini tanlang.")
            return render(request, 'front/checkout.html', {
                'cart': cart,
                'cart_products': cart_products,
                'cart_total': financials['grand_total'],
                'cart_count': cart_count,
                'financials': financials,
                'addresses': addresses,
                'selected_provider': request.POST.get('provider', 'click').strip().lower(),
                'input_phone': phone,
                'selected_address': address,
            })

        if prepayment_percent_raw is not None and str(prepayment_percent_raw).strip() != '':
            try:
                chosen_percent = int(str(prepayment_percent_raw).strip())
            except (ValueError, TypeError):
                messages.error(request, "Noto'g'ri oldindan to'lov foizi kiritildi.")
                return render(request, 'front/checkout.html', {
                    'cart': cart,
                    'cart_products': cart_products,
                    'cart_total': financials['grand_total'],
                    'cart_count': cart_count,
                    'financials': financials,
                    'addresses': addresses,
                    'selected_provider': request.POST.get('provider', 'click').strip().lower(),
                    'input_phone': phone,
                    'selected_address': address,
                })

        try:
            financials = PaymentManager.calculate_order_financials(cart, chosen_percent=chosen_percent)
        except ValueError as e:
            messages.error(request, str(e))
            return render(request, 'front/checkout.html', {
                'cart': cart,
                'cart_products': cart_products,
                'cart_total': financials['grand_total'],
                'cart_count': cart_count,
                'financials': financials,
                'addresses': addresses,
                'selected_provider': request.POST.get('provider', 'click').strip().lower(),
                'input_phone': phone,
                'selected_address': address,
            })

        provider = request.POST.get('provider', models.Payment.Provider.CLICK if financials['prepayment_percent'] > 0 else models.Payment.Provider.CASH).strip().lower()
        payment_method = request.POST.get('payment_method', 'card').strip()

        # Update user profile details
        user.phone = phone
        user.address = address
        user.save(update_fields=['phone', 'address'])

        # Validate stock availability
        for item in cart_products:
            if item.count > item.product.count:
                messages.error(request, f'"{item.product.name}" mahsulotidan omborda yetarli qoldiq mavjud emas (Mavjud: {item.product.count} dona).')
                return redirect('cart')

        # Check prepayment requirements
        if financials['prepayment_percent'] > 0 and provider == models.Payment.Provider.CASH:
            messages.error(request, f"Ushbu buyurtma uchun {financials['prepayment_percent']}% oldindan to'lov talab qilinadi. Iltimos, onlayn to'lov usulini (Click, Payme, Uzum) tanlang.")
            return render(request, 'front/checkout.html', {
                'cart': cart,
                'cart_products': cart_products,
                'cart_total': financials['grand_total'],
                'cart_count': cart_count,
                'financials': financials,
                'addresses': addresses,
                'selected_provider': provider,
                'input_phone': phone,
                'selected_address': address,
            })

        try:
            # Create payment & get checkout URL via PaymentManager
            payment, checkout_url = PaymentManager.create_payment(
                order=cart,
                provider_name=provider,
                chosen_percent=chosen_percent,
                payment_method=payment_method,
                request=request
            )

            # Redirect to provider checkout or success page
            if provider == models.Payment.Provider.CASH:
                messages.success(request, f"Buyurtmangiz muvaffaqiyatli qabul qilindi! Buyurtma kodi: #{str(cart.code)[:8]}")
                return redirect('payment_success', code=cart.code)
            else:
                return redirect(checkout_url)

        except PaymentConfigurationError as e:
            logger.warning("Checkout payment configuration error: %s", e)
            messages.error(request, f"{provider.capitalize()} to'lov tizimi sozlamalari hozirda to'liq o'rnatilmagan. Iltimos, ma'muriyatga murojaat qiling yoki boshqa to'lov usulidan foydalaning.")
            return render(request, 'front/checkout.html', {
                'cart': cart,
                'cart_products': cart_products,
                'cart_total': financials['grand_total'],
                'cart_count': cart_count,
                'financials': financials,
                'addresses': addresses,
                'selected_provider': provider,
                'input_phone': phone,
                'selected_address': address,
            })
        except Exception as e:
            logger.error("Checkout payment creation error: %s", e, exc_info=True)
            messages.error(request, f"To'lovni yaratishda xatolik yuz berdi: {str(e)}")
            return render(request, 'front/checkout.html', {
                'cart': cart,
                'cart_products': cart_products,
                'cart_total': financials['grand_total'],
                'cart_count': cart_count,
                'financials': financials,
                'addresses': addresses,
                'selected_provider': provider,
                'input_phone': phone,
                'selected_address': address,
            })

    # GET request
    default_provider = 'click' if financials['prepayment_percent'] > 0 else 'cash'
    context = {
        'cart': cart,
        'cart_products': cart_products,
        'cart_total': financials['grand_total'],
        'cart_count': cart_count,
        'financials': financials,
        'addresses': addresses,
        'selected_provider': default_provider,
        'input_phone': user.phone or '+998',
        'selected_address': user.address or (addresses.first().name if addresses.exists() else ''),
    }
    return render(request, 'front/checkout.html', context)


@csrf_exempt
def payment_webhook(request, provider):
    """
    To'lov provayderlari (Click, Payme, Uzum) uchun yagona xavfsiz webhook endpoint.
    """
    return PaymentManager.handle_webhook(provider, request)


@login_required(login_url='login')
def payment_success(request, code):
    """
    To'lov muvaffaqiyatli amalga oshirilgandan keyingi sahifa.
    Oldindan to'lov va qoldiq ma'lumotlarini aniq ko'rsatadi.
    """
    order = get_object_or_404(models.Cart, user=request.user, code=code)
    payments = order.payments.all().order_by('-created_at')
    primary_payment = payments.first()
    cart_products = order.cart_products.filter(product__isnull=False).select_related('product', 'product__category')
    financials = PaymentManager.calculate_order_financials(order)

    return render(request, 'front/payment_success.html', {
        'order': order,
        'payment': primary_payment,
        'payments': payments,
        'cart_products': cart_products,
        'financials': financials,
    })


@login_required(login_url='login')
def payment_failed(request, code):
    """
    To'lov amalga oshmay qolganda ko'rsatiladigan xatolik sahifasi.
    """
    order = get_object_or_404(models.Cart, user=request.user, code=code)
    payment = order.payments.first()
    financials = PaymentManager.calculate_order_financials(order)

    return render(request, 'front/payment_failed.html', {
        'order': order,
        'payment': payment,
        'financials': financials,
    })


@login_required(login_url='login')
@require_POST
def retry_payment(request, code):
    """
    Muvaffaqiyatsiz bo'lgan yoki to'lanmagan buyurtma uchun to'lovni qayta boshlash.
    """
    order = get_object_or_404(models.Cart, user=request.user, code=code)
    provider = request.POST.get('provider', models.Payment.Provider.CLICK).strip().lower()

    try:
        payment, checkout_url = PaymentManager.create_payment(
            order=order,
            provider_name=provider,
            purpose=models.Payment.Purpose.PREPAYMENT if order.prepayment_percent in (30, 50) else models.Payment.Purpose.FULL,
            request=request
        )
        return redirect(checkout_url)
    except PaymentConfigurationError as e:
        messages.error(request, f"{provider.capitalize()} to'lov tizimi sozlanmagan. Iltimos, boshqa to'lov turidan foydalaning.")
        return redirect('order_detail', code=order.code)
    except Exception as e:
        messages.error(request, f"To'lovni qayta boshlashda xatolik: {str(e)}")
        return redirect('order_detail', code=order.code)


@login_required(login_url='login')
@require_POST
def pay_balance(request, code):
    """
    Mijoz tomonidan buyurtmaning qolgan qoldiq summasini onlayn to'lash.
    """
    order = get_object_or_404(models.Cart, user=request.user, code=code, status__gt=1)
    if order.remaining_amount <= Decimal('0.00'):
        messages.info(request, "Ushbu buyurtma allaqachon to'liq to'langan.")
        return redirect('order_detail', code=order.code)

    provider = request.POST.get('provider', models.Payment.Provider.CLICK).strip().lower()
    if provider == models.Payment.Provider.CASH:
        messages.info(request, "Qoldiq summa buyurtma yetkazilganda kuryerga naqd yoki karta orqali to'lanadi.")
        return redirect('order_detail', code=order.code)

    try:
        payment, checkout_url = PaymentManager.create_payment(
            order=order,
            provider_name=provider,
            purpose=models.Payment.Purpose.BALANCE,
            request=request
        )
        return redirect(checkout_url)
    except PaymentConfigurationError as e:
        messages.error(request, f"{provider.capitalize()} to'lov tizimi sozlanmagan. Iltimos, kuryer yetkazganda naqd to'lang yoki boshqa tizimni tanlang.")
        return redirect('order_detail', code=order.code)
    except Exception as e:
        messages.error(request, f"Qoldiq to'lovni boshlashda xatolik: {str(e)}")
        return redirect('order_detail', code=order.code)


@login_required(login_url='login')
def order_history(request):
    return redirect('/profile/?tab=orders')


@login_required(login_url='login')
def order_detail(request, code):
    order = get_object_or_404(models.Cart, user=request.user, code=code, status__gt=1)
    cart_products = order.cart_products.filter(product__isnull=False)
    payments = order.payments.all().order_by('-created_at')
    primary_payment = payments.first()
    financials = PaymentManager.calculate_order_financials(order)

    return render(request, 'front/order_detail.html', {
        'order': order,
        'cart_products': cart_products,
        'payment': primary_payment,
        'payments': payments,
        'financials': financials,
    })


def live_search(request):
    q = (request.GET.get('q') or request.GET.get('search') or '').strip()
    if not q:
        return JsonResponse({'results': []})

    from django.db.models import Q
    products = models.Product.objects.filter(
        Q(name__icontains=q) | Q(category__name__icontains=q) | Q(code__icontains=q)
    ).select_related('category').distinct()[:8]

    results = []
    for p in products:
        results.append({
            'code': p.code,
            'name': p.name,
            'category': p.category.name if p.category else '',
            'price': f"{int(p.active_price):,} so'm".replace(',', ' '),
            'image': p.image.url if p.image else '/static/assets/images/thumbs/product-img1.png',
            'url': f"/product-detail/{p.code}/",
            'discount_percent': p.discount_percent,
        })
    return JsonResponse({'results': results})


def category_products_api(request, category_id):
    from django.template.loader import render_to_string
    category = get_object_or_404(models.Category, id=category_id)
    try:
        offset = int(request.GET.get('offset', 0))
        limit = int(request.GET.get('limit', 10))
    except (ValueError, TypeError):
        offset = 0
        limit = 10

    products_qs = models.Product.objects.filter(category=category).order_by('-created_at')
    total_count = products_qs.count()
    products = list(products_qs[offset:offset + limit])
    has_more = (offset + len(products)) < total_count

    cart_ids = []
    wishlist_ids = []
    if request.user.is_authenticated:
        cart = models.Cart.objects.filter(user=request.user, status=1).first()
        if cart:
            cart_ids = list(models.CartProduct.objects.filter(cart=cart).values_list('product_id', flat=True))
        wishlist_ids = list(models.WishList.objects.filter(user=request.user).values_list('product_id', flat=True))

    html_items = []
    for p in products:
        item_html = render_to_string('front/partials/product_card.html', {
            'product': p,
            'cart_ids': cart_ids,
            'wishlist_ids': wishlist_ids,
            'request': request
        })
        html_items.append(item_html)

    return JsonResponse({
        'html': ''.join(html_items),
        'has_more': has_more,
        'next_offset': offset + len(products),
        'total_count': total_count
    })


def check_username_api(request):
    username = request.GET.get('username', '').strip()
    if not username:
        return JsonResponse({'valid': False, 'message': 'Foydalanuvchi nomi kiritilmadi'})
    if len(username) < 4:
        return JsonResponse({'valid': False, 'message': "Kamida 4 ta belgidan iborat bo'lishi kerak"})

    is_taken = models.User.objects.filter(username__iexact=username).exists()
    if is_taken:
        return JsonResponse({'valid': False, 'available': False, 'message': 'Ushbu nom allaqachon band!'})

    return JsonResponse({'valid': True, 'available': True, 'message': "Ushbu nom bo'sh (ishlatishingiz mumkin)"})


def check_phone_api(request):
    raw_phone = request.GET.get('phone', '').strip().replace(' ', '').replace('-', '').replace('+', '')
    if raw_phone.startswith('998'):
        phone = '+' + raw_phone
        digits = raw_phone[3:]
    else:
        phone = '+998' + raw_phone
        digits = raw_phone

    if len(digits) != 9 or not digits.isdigit():
        return JsonResponse({'valid': False, 'message': "9 ta raqam bo'lishi kerak"})

    is_taken = models.User.objects.filter(phone=phone).exists()
    if is_taken:
        return JsonResponse({'valid': False, 'available': False, 'message': "Ushbu telefon raqami allaqachon ro'yxatdan o'tgan!"})

    return JsonResponse({'valid': True, 'available': True, 'message': "Ushbu telefon raqami bo'sh"})
