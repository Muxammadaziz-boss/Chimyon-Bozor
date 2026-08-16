import json
import re
import random
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Count, Q, Avg, Sum, Case, When, DecimalField, IntegerField, FloatField, Value, Min, Max, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, render, redirect
from django.http import JsonResponse, HttpResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import models
from .utils import paginate_queryset
from .sms_service import send_sms_code
from .validators import validate_image_file, normalize_uz_phone, PHONE_RE
from .services.payment import PaymentManager
from .services.payment.base import PaymentConfigurationError
from django.contrib.auth import authenticate, login, logout
from django.urls import reverse
from django.conf import settings

logger = logging.getLogger(__name__)
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
    
    similar_category = models.Category.objects.filter(is_active=True).exclude(id=product.category.id).order_by('?').first()
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
    """
    Advanced multi-criteria filtering, sorting, price range, stock, rating, and chips pipeline.
    """
    # 1. Search Query
    search_query = (request.GET.get('q') or request.GET.get('query') or request.GET.get('search') or '').strip()
    if search_query:
        products_qs = products_qs.filter(
            Q(name__icontains=search_query) |
            Q(category__name__icontains=search_query) |
            Q(code__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    # 2. Category selection from GET param if not directly passed
    selected_category_id = request.GET.get('category')
    if selected_category_id and not active_category:
        try:
            cat_id_int = int(selected_category_id)
            active_category = models.Category.objects.filter(id=cat_id_int, is_active=True).first()
            if active_category:
                products_qs = products_qs.filter(category=active_category)
        except (ValueError, TypeError):
            pass

    # 3. Price Filtering
    min_price_raw = request.GET.get('min_price', '').strip()
    max_price_raw = request.GET.get('max_price', '').strip()
    min_price = None
    max_price = None

    if min_price_raw:
        clean_min = re.sub(r'\D', '', min_price_raw)
        try:
            if clean_min:
                min_price_val = Decimal(clean_min)
                if min_price_val >= 0:
                    min_price = min_price_val
                    min_price_raw = clean_min
                else:
                    min_price_raw = ''
            else:
                min_price_raw = ''
        except Exception:
            min_price_raw = ''

    if max_price_raw:
        clean_max = re.sub(r'\D', '', max_price_raw)
        try:
            if clean_max:
                max_price_val = Decimal(clean_max)
                if max_price_val >= 0:
                    max_price = max_price_val
                    max_price_raw = clean_max
                else:
                    max_price_raw = ''
            else:
                max_price_raw = ''
        except Exception:
            max_price_raw = ''

    # Swap if min > max
    if min_price is not None and max_price is not None and min_price > max_price:
        min_price, max_price = max_price, min_price

    if min_price is not None:
        products_qs = products_qs.filter(
            Q(discount_status=True, discount_price__isnull=False, discount_price__gte=min_price) |
            Q(Q(discount_status=False) | Q(discount_price__isnull=True), price__gte=min_price)
        )

    if max_price is not None:
        products_qs = products_qs.filter(
            Q(discount_status=True, discount_price__isnull=False, discount_price__lte=max_price) |
            Q(Q(discount_status=False) | Q(discount_price__isnull=True), price__lte=max_price)
        )

    # 4. Discount Filter
    discount_filter = request.GET.get('discount', '').strip()
    if discount_filter in ('1', 'only', 'true'):
        products_qs = products_qs.filter(discount_status=True, discount_price__isnull=False)
    elif discount_filter == '10':
        products_qs = products_qs.filter(
            discount_status=True,
            discount_price__isnull=False,
            discount_price__lte=F('price') * Decimal('0.90')
        )
    elif discount_filter == '20':
        products_qs = products_qs.filter(
            discount_status=True,
            discount_price__isnull=False,
            discount_price__lte=F('price') * Decimal('0.80')
        )
    elif discount_filter == '50':
        products_qs = products_qs.filter(
            discount_status=True,
            discount_price__isnull=False,
            discount_price__lte=F('price') * Decimal('0.50')
        )

    # 5. Stock Filter
    stock_filter = request.GET.get('stock', '').strip()
    if stock_filter == 'in_stock':
        products_qs = products_qs.filter(count__gt=0)
    elif stock_filter == 'low_stock':
        products_qs = products_qs.filter(count__gt=0, count__lte=5)
    elif stock_filter == 'out_of_stock':
        products_qs = products_qs.filter(count__lte=0)

    # 6. Sales, Rating & Price Subqueries / Annotations
    sales_subquery = models.CartProduct.objects.filter(
        product=OuterRef('pk'),
        cart__status__in=[2, 3, 4]
    ).values('product').annotate(total=Sum('count')).values('total')

    rating_subquery = models.Review.objects.filter(
        product=OuterRef('pk')
    ).values('product').annotate(avg_r=Avg('rating')).values('avg_r')

    reviews_count_subquery = models.Review.objects.filter(
        product=OuterRef('pk')
    ).values('product').annotate(c=Count('id')).values('c')

    rating_filter = request.GET.get('rating', '').strip()
    products_qs = products_qs.annotate(
        total_sales=Coalesce(Subquery(sales_subquery, output_field=IntegerField()), Value(0)),
        avg_rating=Coalesce(Subquery(rating_subquery, output_field=FloatField()), Value(0.0, output_field=FloatField())),
        reviews_count=Coalesce(Subquery(reviews_count_subquery, output_field=IntegerField()), Value(0)),
        in_stock_rank=Case(When(count__gt=0, then=Value(1)), default=Value(0), output_field=IntegerField()),
        effective_price=Case(
            When(discount_status=True, discount_price__isnull=False, then='discount_price'),
            default='price',
            output_field=DecimalField(max_digits=10, decimal_places=2)
        )
    )

    if rating_filter == '4':
        products_qs = products_qs.filter(avg_rating__gte=4.0)
    elif rating_filter == '3':
        products_qs = products_qs.filter(avg_rating__gte=3.0)

    # 7. Sorting
    sort = request.GET.get('sort', 'recommended').strip()
    if sort == 'newest':
        products_qs = products_qs.order_by('-created_at', '-id')
    elif sort == 'price_asc':
        products_qs = products_qs.order_by('effective_price', 'price', 'id')
    elif sort == 'price_desc':
        products_qs = products_qs.order_by('-effective_price', '-price', '-id')
    elif sort == 'popular':
        products_qs = products_qs.order_by('-total_sales', '-created_at', '-id')
    elif sort == 'rating':
        products_qs = products_qs.order_by('-avg_rating', '-reviews_count', '-created_at', '-id')
    else:  # recommended
        # Deterministic recommendation: in-stock items first, confirmed sales, reviews, ratings, newest
        products_qs = products_qs.order_by('-in_stock_rank', '-total_sales', '-reviews_count', '-avg_rating', '-created_at', '-id')

    # View Mode (Grid vs List)
    view_mode = request.GET.get('view', 'grid').strip()
    if view_mode not in ('grid', 'list'):
        view_mode = 'grid'

    # 8. Active Filter Chips
    active_chips = []
    if active_category:
        active_chips.append({
            'type': 'category',
            'label': f"Kategoriya: {active_category.name}",
            'remove_key': 'category',
        })
    if search_query:
        active_chips.append({
            'type': 'q',
            'label': f"Qidiruv: \"{search_query}\"",
            'remove_key': 'q',
        })
    if min_price is not None or max_price is not None:
        min_label = f"{min_price:,.0f}".replace(',', ' ') if min_price is not None else "0"
        max_label = f"{max_price:,.0f}".replace(',', ' ') if max_price is not None else "∞"
        active_chips.append({
            'type': 'price',
            'label': f"Narx: {min_label} - {max_label} so'm",
            'remove_key': 'price',
        })
    if discount_filter:
        disc_labels = {
            '1': 'Chegirmali',
            'only': 'Chegirmali',
            'true': 'Chegirmali',
            '10': '10%+ chegirma',
            '20': '20%+ chegirma',
            '50': '50%+ chegirma'
        }
        active_chips.append({
            'type': 'discount',
            'label': f"Chegirma: {disc_labels.get(discount_filter, discount_filter)}",
            'remove_key': 'discount',
        })
    if stock_filter:
        stock_labels = {
            'in_stock': 'Mavjud mahsulotlar',
            'low_stock': 'Kam qolgan (1-5 ta)',
            'out_of_stock': 'Tugagan mahsulotlar'
        }
        if stock_filter in stock_labels:
            active_chips.append({
                'type': 'stock',
                'label': f"Holat: {stock_labels[stock_filter]}",
                'remove_key': 'stock',
            })
    if rating_filter:
        active_chips.append({
            'type': 'rating',
            'label': f"Reyting: {rating_filter}+ yulduz",
            'remove_key': 'rating',
        })

    # Global Price Range for sliders (active categories only)
    price_aggregate = models.Product.objects.filter(category__is_active=True).aggregate(min_p=Min('price'), max_p=Max('price'))
    global_min_price = int(price_aggregate['min_p'] or 0)
    global_max_price = int(price_aggregate['max_p'] or 2000000)

    # Categories with count for sidebar
    sidebar_categories = list(
        models.Category.objects.filter(is_active=True)
        .annotate(product_count=Count('product'))
        .order_by('name')
    )

    products_qs = products_qs.select_related('category')
    total_matching_products = products_qs.count()
    page_obj = paginate_queryset(request, products_qs, per_page=20)

    # SEO indexing policy & canonical URL calculation
    is_filtered = bool(search_query or min_price or max_price or discount_filter or stock_filter or rating_filter or (sort and sort != 'recommended'))
    page_num = request.GET.get('page')
    is_paginated = bool(page_num and str(page_num).strip() not in ('1', ''))
    is_thin_or_duplicate_seo = is_filtered or is_paginated

    if active_category:
        canonical_path = reverse('category_filter', kwargs={'category_id': active_category.id})
    else:
        canonical_path = reverse('all_products')
    canonical_url = request.build_absolute_uri(canonical_path)

    context = {
        'categories': sidebar_categories,
        'top_categories': sidebar_categories[:7],
        'products': page_obj.object_list,
        'page_obj': page_obj,
        'active_category': active_category.id if active_category else None,
        'active_category_name': active_category.name if active_category else None,
        'total_products': total_matching_products,
        'search_query': search_query,
        'min_price': min_price,
        'max_price': max_price,
        'min_price_raw': min_price_raw,
        'max_price_raw': max_price_raw,
        'global_min_price': global_min_price,
        'global_max_price': global_max_price,
        'discount_filter': discount_filter,
        'stock_filter': stock_filter,
        'rating_filter': rating_filter,
        'sort': sort,
        'view_mode': view_mode,
        'active_chips': active_chips,
        'active_chips_count': len(active_chips),
        'is_thin_or_duplicate_seo': is_thin_or_duplicate_seo,
        'canonical_url': canonical_url,
    }
    _attach_cart_wishlist_context(request, context)
    return context


def category_filter(request, category_id):
    active_category = get_object_or_404(models.Category, id=category_id, is_active=True)
    products_qs = models.Product.objects.filter(category=active_category)
    context = _build_catalog_context(request, products_qs, active_category=active_category)
    return render(request, 'front/category_filter.html', context)


def all_products(request):
    products_qs = models.Product.objects.filter(category__is_active=True)
    context = _build_catalog_context(request, products_qs)
    return render(request, 'front/category_filter.html', context)


def categories_page(request):
    """
    Barcha kategoriyalar katalogi, qidiruv va guruhlash sahifasi.
    """
    search_query = (request.GET.get('q') or request.GET.get('search') or request.GET.get('query') or '').strip()
    
    categories_qs = (
        models.Category.objects.filter(is_active=True)
        .annotate(
            product_count=Count('product')
        )
        .order_by('-product_count', 'name')
    )

    if search_query:
        categories_qs = categories_qs.filter(name__icontains=search_query)

    categories_list = list(categories_qs)

    # Category Groupings mapping helper
    GROUP_MAPPING = [
        {
            'id': 'clothing',
            'title': 'Kiyim-kechak & Moda',
            'icon': 'fas fa-tshirt',
            'keywords': ['kiyim', 'ko\'ylak', 'libos', 'poyabzal', 'shim', 'kostyum', 'aksessuar', 'zargarlik', 'soat']
        },
        {
            'id': 'tech',
            'title': 'Elektronika & Gadjetlar',
            'icon': 'fas fa-mobile-alt',
            'keywords': ['smartfon', 'telefon', 'gadjet', 'noutbuk', 'kompyuter', 'elektronika', 'texnika', 'maishiy']
        },
        {
            'id': 'home',
            'title': "Uy, Ro'zg'or & Ta'mirlash",
            'icon': 'fas fa-home',
            'keywords': ['uy', 'ro\'zg\'or', 'oshxona', 'mebel', 'interyer', 'qurilish', 'ta\'mirlash', 'anjom']
        },
        {
            'id': 'beauty',
            'title': "Go'zallik & Parvarish",
            'icon': 'fas fa-heart',
            'keywords': ['kosmetika', 'parvarish', 'parfyumeriya', 'atir', 'go\'zallik', 'shaxsiy']
        },
        {
            'id': 'sport_kids',
            'title': "Sport, Bolalar & Hordiq",
            'icon': 'fas fa-futbol',
            'keywords': ['sport', 'hordiq', 'bolalar', 'o\'yinchoq', 'o\'yin', 'shirinlik']
        },
        {
            'id': 'auto_books',
            'title': "Avto, Kitoblar & Kantselyariya",
            'icon': 'fas fa-book',
            'keywords': ['avto', 'kitob', 'darslik', 'kantselyariya', 'daftar', 'qalam']
        }
    ]

    grouped_categories = []
    categorized_ids = set()

    for group_meta in GROUP_MAPPING:
        matched = []
        for cat in categories_list:
            cat_name_lower = cat.name.lower()
            if any(kw in cat_name_lower for kw in group_meta['keywords']):
                matched.append(cat)
                categorized_ids.add(cat.id)
        if matched:
            grouped_categories.append({
                'id': group_meta['id'],
                'title': group_meta['title'],
                'icon': group_meta['icon'],
                'categories': matched,
            })

    # Any remaining categories
    other_cats = [cat for cat in categories_list if cat.id not in categorized_ids]
    if other_cats:
        grouped_categories.append({
            'id': 'others',
            'title': 'Boshqa Kategoriyalar',
            'icon': 'fas fa-boxes',
            'categories': other_cats,
        })

    total_products = models.Product.objects.filter(category__is_active=True).count()

    return render(request, 'front/categories.html', {
        'categories': categories_list,
        'grouped_categories': grouped_categories,
        'search_query': search_query,
        'total_categories': len(categories_list),
        'total_products': total_products,
    })


def register(request):
    if request.method == "POST":
        raw_phone = request.POST.get('phone', '').strip()
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        confirm_password = request.POST.get('confirm_password', '')

        phone = normalize_uz_phone(raw_phone)

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

        if not phone:
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
            if len(new_username) < 4:
                messages.error(request, "Foydalanuvchi nomi kamida 4 ta belgidan iborat bo'lishi kerak")
                return redirect('profile')
            if models.User.objects.filter(username__iexact=new_username).exclude(pk=user.pk).exists():
                messages.error(request, 'Bu foydalanuvchi nomi band')
                return redirect('profile')
            user.username = new_username

        raw_phone = (request.POST.get('phone') or '').strip()
        if raw_phone:
            clean_phone = normalize_uz_phone(raw_phone)
            if not clean_phone:
                messages.error(request, "Telefon raqamini +998XXXXXXXXX formatida to'g'ri kiriting.")
                return redirect('profile')

            if clean_phone != user.phone:
                if models.User.objects.filter(phone=clean_phone).exclude(pk=user.pk).exists():
                    messages.error(request, "Bu telefon raqami allaqachon ro'yxatdan o'tgan!")
                    return redirect('profile')
                user.phone = clean_phone

        user.last_name = request.POST.get('last_name', '').strip()
        user.first_name = request.POST.get('first_name', '').strip()
        user.address = request.POST.get('address', '').strip()
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
        return redirect('profile')

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
    cart = models.Cart.objects.filter(user=request.user, status=1).first()
    if cart:
        models.CartProduct.objects.filter(cart=cart, product=product).delete()

    if is_ajax:
        active_items = list(cart.cart_products.filter(product__isnull=False, product__count__gt=0)) if cart else []
        has_out_of_stock = cart.cart_products.filter(product__count__lte=0).exists() if cart else False
        return JsonResponse({
            'status': 'success',
            'message': f'"{product.name}" savatdan olib tashlandi',
            'cart_count': sum(item.count for item in active_items),
            'cart_total': float(sum(item.total_price for item in active_items)),
            'cart_items_count': cart.cart_products.count() if cart else 0,
            'has_out_of_stock': has_out_of_stock,
            'in_cart': False
        })
    messages.success(request, f'"{product.name}" savatdan olib tashlandi')
    return redirect_back(request, 'cart')


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

        if quantity < 0:
            if request.content_type == 'application/json':
                return JsonResponse({'status': 'error', 'message': "Noto'g'ri miqdor kiritildi"}, status=400)
            messages.error(request, "Noto'g'ri miqdor kiritildi")
            return redirect('cart')

        # Check if product is out of stock (stock <= 0)
        if product.count <= 0:
            if quantity == 0:
                cart_product.delete()
                active_items = list(cart.cart_products.filter(product__isnull=False, product__count__gt=0))
                has_out_of_stock = cart.cart_products.filter(product__count__lte=0).exists()
                if request.content_type == 'application/json':
                    return JsonResponse({
                        'status': 'deleted',
                        'cart_total': float(sum(item.total_price for item in active_items)),
                        'cart_count': sum(item.count for item in active_items),
                        'cart_items_count': cart.cart_products.count(),
                        'has_out_of_stock': has_out_of_stock,
                    })
                messages.success(request, f'"{product.name}" savatdan olib tashlandi')
                return redirect('cart')

            if request.content_type == 'application/json':
                return JsonResponse({
                    'status': 'error',
                    'out_of_stock': True,
                    'message': f'"{product.name}" omborda tugagan.',
                    'max_stock': 0
                }, status=400)
            messages.error(request, f'"{product.name}" omborda tugagan.')
            return redirect('cart')

        stock_warning = None
        if quantity > product.count:
            quantity = product.count
            stock_warning = f"Faqat {product.count} dona mavjud."

        if quantity <= 0:
            cart_product.delete()
            active_items = list(cart.cart_products.filter(product__isnull=False, product__count__gt=0))
            has_out_of_stock = cart.cart_products.filter(product__count__lte=0).exists()
            if request.content_type == 'application/json':
                return JsonResponse({
                    'status': 'deleted',
                    'cart_total': float(sum(item.total_price for item in active_items)),
                    'cart_count': sum(item.count for item in active_items),
                    'cart_items_count': cart.cart_products.count(),
                    'has_out_of_stock': has_out_of_stock,
                })
            messages.success(request, f'"{product.name}" savatdan olib tashlandi')
            return redirect('cart')

        cart_product.count = quantity
        cart_product.save(update_fields=['count'])

        active_items = list(cart.cart_products.filter(product__isnull=False, product__count__gt=0))
        has_out_of_stock = cart.cart_products.filter(product__count__lte=0).exists()
        if request.content_type == 'application/json':
            return JsonResponse({
                'status': 'updated',
                'item_total_price': float(cart_product.total_price),
                'count': cart_product.count,
                'unit_price': float(cart_product.unit_price),
                'max_stock': product.count,
                'stock_warning': stock_warning,
                'cart_total': float(sum(item.total_price for item in active_items)),
                'cart_count': sum(item.count for item in active_items),
                'cart_items_count': cart.cart_products.count(),
                'has_out_of_stock': has_out_of_stock,
            })
        if stock_warning:
            messages.warning(request, stock_warning)
        else:
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
    # Remove any dangling cart products where product is null
    models.CartProduct.objects.filter(cart__user=request.user, cart__status=1, product__isnull=True).delete()

    cart_products = list(models.CartProduct.objects.filter(
        cart__user=request.user,
        cart__status=1,
        product__isnull=False,
    ).select_related('product', 'product__category', 'cart'))

    adjusted_messages = []
    has_out_of_stock = False

    for item in cart_products:
        if item.product.count <= 0:
            has_out_of_stock = True
        elif item.count > item.product.count:
            item.count = item.product.count
            item.save(update_fields=['count'])
            adjusted_messages.append(f'"{item.product.name}" mahsulotidan omborda faqat {item.product.count} dona qolganligi sababli savat miqdori moslashtirildi.')

    for msg in adjusted_messages:
        messages.warning(request, msg)

    # In-stock active products for valid order calculations
    active_cart_products = [item for item in cart_products if item.product and item.product.count > 0]
    cart_total = sum(item.total_price for item in active_cart_products)
    cart_count = sum(item.count for item in active_cart_products)

    context = {
        "cart_products": cart_products,
        "cart_total": cart_total,
        "cart_count": cart_count,
        "has_out_of_stock": has_out_of_stock,
    }
    return render(request, 'front/cart.html', context=context)


@login_required(login_url='login')
def checkout(request):
    """
    Checkout sahifasi (GET) va Buyurtma/To'lov yaratish (POST).
    Multi-tab va stale cart holatlariga qarshi qat'iy tekshiruvlar bilan.
    """
    user = request.user
    cart = models.Cart.objects.filter(user=user, status=1).first()
    if not cart or not cart.cart_products.filter(product__isnull=False).exists():
        messages.error(request, "Savatingiz bo'sh. Iltimos, avval mahsulot tanlang.")
        return redirect('cart')

    # Clean up any null products
    cart.cart_products.filter(product__isnull=True).delete()
    cart_products = list(cart.cart_products.filter(product__isnull=False).select_related('product', 'product__category'))

    # Stale stock & Out-of-stock validation
    stock_adjusted = False
    for item in cart_products:
        if item.product.count <= 0:
            messages.error(request, f'"{item.product.name}" mahsuloti omborda tugagan. Buyurtma berish uchun uni savatdan olib tashlang.')
            return redirect('cart')
        elif item.count > item.product.count:
            item.count = item.product.count
            item.save(update_fields=['count'])
            stock_adjusted = True

    if stock_adjusted:
        messages.warning(request, "Omborda qoldiq o'zgarganligi sababli savatingiz miqdori moslashtirildi.")
        return redirect('cart')

    cart_count = sum(item.count for item in cart_products)
    financials = PaymentManager.calculate_order_financials(cart)
    addresses = models.Address.objects.filter(is_active=True).order_by('name')
    valid_address_names = list(addresses.values_list('name', flat=True)) if addresses.exists() else []

    if request.method == 'POST':
        raw_phone = request.POST.get('phone', '').strip()
        if not raw_phone and user.phone:
            raw_phone = str(user.phone).strip()

        address = request.POST.get('address', '').strip()
        if not address and user.address:
            address = str(user.address).strip()

        provider = request.POST.get('provider', 'click').strip().lower()
        prepayment_percent_raw = request.POST.get('prepayment_percent')
        chosen_percent = None

        # Store draft for PRG redirect
        request.session['checkout_draft'] = {
            'phone': raw_phone,
            'address': address,
            'provider': provider,
            'prepayment_percent': prepayment_percent_raw,
        }

        phone = normalize_uz_phone(raw_phone)
        if not phone:
            messages.error(request, "Telefon raqami noto'g'ri kiritildi. Iltimos, to'g'ri O'zbekiston raqamini kiriting (masalan: +998 90 123 45 67).")
            return redirect('checkout')

        # Validate Address
        if not address:
            messages.error(request, "Iltimos, yetkazib berish manzilini tanlang.")
            return redirect('checkout')

        if addresses.exists() and address not in valid_address_names:
            messages.error(request, "Iltimos, admin tomonidan qo'shilgan tasdiqlangan manzillardan birini tanlang.")
            return redirect('checkout')

        if prepayment_percent_raw is not None and str(prepayment_percent_raw).strip() != '':
            try:
                chosen_percent = int(str(prepayment_percent_raw).strip())
            except (ValueError, TypeError):
                messages.error(request, "Noto'g'ri oldindan to'lov foizi kiritildi.")
                return redirect('checkout')

        try:
            financials = PaymentManager.calculate_order_financials(cart, chosen_percent=chosen_percent)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('checkout')

        payment_method = request.POST.get('payment_method', 'card').strip()

        # Update user profile details
        user.phone = phone
        user.address = address
        user.save(update_fields=['phone', 'address'])

        # Multi-tab / Concurrent stock verification: atomic refresh from DB
        for item in cart_products:
            item.product.refresh_from_db()
            if item.product.count <= 0:
                messages.error(request, f'"{item.product.name}" mahsuloti omborda tugagan.')
                return redirect('cart')
            if item.count > item.product.count:
                item.count = item.product.count
                item.save(update_fields=['count'])
                messages.warning(request, f'"{item.product.name}" mahsulotidan omborda yetarli qoldiq qolmagan (Mavjud: {item.product.count} dona).')
                return redirect('cart')

        # Check prepayment requirements
        if financials['prepayment_percent'] > 0 and provider == models.Payment.Provider.CASH:
            messages.error(request, f"Ushbu buyurtma uchun {financials['prepayment_percent']}% oldindan to'lov talab qilinadi. Iltimos, onlayn to'lov usulini (Click, Payme, Uzum) tanlang.")
            return redirect('checkout')

        try:
            # Create payment & get checkout URL via PaymentManager
            payment, checkout_url = PaymentManager.create_payment(
                order=cart,
                provider_name=provider,
                chosen_percent=chosen_percent,
                payment_method=payment_method,
                request=request
            )

            # Clear session draft upon successful checkout creation
            request.session.pop('checkout_draft', None)

            # Redirect to provider checkout or success page (100% PRG)
            if provider == models.Payment.Provider.CASH:
                messages.success(request, f"Buyurtmangiz muvaffaqiyatli qabul qilindi! Buyurtma kodi: #{str(cart.code)[:8]}")
                return redirect('payment_success', code=cart.code)
            else:
                return redirect(checkout_url)

        except PaymentConfigurationError as e:
            logger.warning("Checkout payment configuration error: %s", e)
            messages.error(request, f"{provider.capitalize()} to'lov tizimi sozlamalari hozirda to'liq o'rnatilmagan. Iltimos, ma'muriyatga murojaat qiling yoki boshqa to'lov usulidan foydalaning.")
            return redirect('checkout')
        except Exception as e:
            logger.error("Checkout payment creation error: %s", e, exc_info=True)
            messages.error(request, f"To'lovni yaratishda xatolik yuz berdi: {str(e)}")
            return redirect('checkout')

    # GET request handler (Pure PRG)
    draft = request.session.pop('checkout_draft', {})
    draft_percent = draft.get('prepayment_percent')
    if draft_percent is not None and str(draft_percent).strip() != '':
        try:
            financials = PaymentManager.calculate_order_financials(cart, chosen_percent=int(draft_percent))
        except ValueError:
            pass

    default_provider = draft.get('provider') or ('click' if financials['prepayment_percent'] > 0 else 'cash')
    
    draft_address = draft.get('address')
    if draft_address and draft_address in valid_address_names:
        initial_address = draft_address
    elif user.address and user.address in valid_address_names:
        initial_address = user.address
    elif addresses.exists():
        initial_address = addresses.first().name
    else:
        initial_address = ''

    context = {
        'cart': cart,
        'cart_products': cart_products,
        'cart_total': financials['grand_total'],
        'cart_count': cart_count,
        'financials': financials,
        'addresses': addresses,
        'selected_provider': default_provider,
        'input_phone': draft.get('phone') or user.phone or '+998',
        'selected_address': initial_address,
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
    
    # Qoldiq mavjudligini Decimal asosida tekshirish
    if order.remaining_amount <= Decimal('0.00'):
        messages.info(request, "Ushbu buyurtma allaqachon to'liq to'langan.")
        return redirect('order_detail', code=order.code)

    provider = (request.POST.get('provider') or models.Payment.Provider.CLICK).strip().lower()
    valid_online_providers = [
        models.Payment.Provider.CLICK,
        models.Payment.Provider.PAYME,
        models.Payment.Provider.UZUM
    ]

    if provider not in valid_online_providers:
        if provider == models.Payment.Provider.CASH:
            messages.info(request, "Qoldiq summa buyurtma yetkazilganda kuryerga naqd yoki karta orqali to'lanadi.")
        else:
            messages.error(request, "Noto'g'ri to'lov provayderi tanlandi. Iltimos, Click, Payme yoki Uzum tizimlaridan birini tanlang.")
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
        logger.warning("Payment configuration error in pay_balance: %s", str(e))
        messages.error(request, f"{provider.upper()} to'lov tizimi sozlanmagan yoki test rejimida. Iltimos, boshqa to'lov usulini tanlang.")
        return redirect('order_detail', code=order.code)
    except Exception as e:
        logger.exception("Error initiating balance payment for order #%s: %s", order.code, str(e))
        messages.error(request, "Qoldiq to'lovni boshlashda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
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

    # Kutilayotgan / boshlangan to'lovlarni hisoblash
    pending_payment = payments.filter(
        status__in=[models.Payment.Status.INITIATED, models.Payment.Status.PENDING]
    ).first()
    pending_amount = pending_payment.amount if pending_payment else Decimal('0.00')

    return render(request, 'front/order_detail.html', {
        'order': order,
        'cart_products': cart_products,
        'payment': primary_payment,
        'payments': payments,
        'financials': financials,
        'pending_payment': pending_payment,
        'pending_amount': pending_amount,
    })


def live_search(request):
    q = (request.GET.get('q') or request.GET.get('search') or '').strip()
    if not q:
        return JsonResponse({'results': []})

    from django.db.models import Q
    products = models.Product.objects.filter(category__is_active=True).filter(
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
        return JsonResponse({'valid': False, 'available': False, 'message': "Foydalanuvchi nomi kiritilmadi"})
    if len(username) < 4:
        return JsonResponse({'valid': False, 'available': False, 'message': "Kamida 4 ta belgidan iborat bo'lishi kerak"})

    qs = models.User.objects.filter(username__iexact=username)
    if request.user.is_authenticated:
        if request.user.username.lower() == username.lower():
            return JsonResponse({'valid': True, 'available': True, 'is_current': True, 'message': "Sizning joriy foydalanuvchi nomingiz"})
        qs = qs.exclude(pk=request.user.pk)

    is_taken = qs.exists()
    if is_taken:
        return JsonResponse({'valid': False, 'available': False, 'message': 'Ushbu nom allaqachon band!'})

    return JsonResponse({'valid': True, 'available': True, 'message': "Ushbu nom bo'sh (ishlatishingiz mumkin)"})


def check_phone_api(request):
    raw_phone = request.GET.get('phone', '').strip()
    if not raw_phone:
        return JsonResponse({'valid': False, 'available': False, 'message': "Telefon raqam kiritilmadi"})

    phone = normalize_uz_phone(raw_phone)
    if not phone:
        return JsonResponse({'valid': False, 'available': False, 'message': "Telefon raqamini +998XXXXXXXXX formatida kiriting"})

    qs = models.User.objects.filter(phone=phone)
    if request.user.is_authenticated:
        if request.user.phone == phone:
            return JsonResponse({'valid': True, 'available': True, 'is_current': True, 'canonical': phone, 'message': "Sizning joriy telefon raqamingiz"})
        qs = qs.exclude(pk=request.user.pk)

    is_taken = qs.exists()
    if is_taken:
        return JsonResponse({'valid': False, 'available': False, 'canonical': phone, 'message': "Ushbu telefon raqami allaqachon ro'yxatdan o'tgan!"})

    return JsonResponse({'valid': True, 'available': True, 'canonical': phone, 'message': "Telefon raqami to'g'ri va bo'sh"})


def custom_404_view(request, exception=None):
    return render(request, '404.html', status=404)


def custom_403_view(request, exception=None):
    return render(request, '403.html', status=403)


def custom_500_view(request):
    try:
        return render(request, '500.html', status=500)
    except Exception:
        from django.http import HttpResponseServerError
        from django.template import loader
        t = loader.get_template('500.html')
        return HttpResponseServerError(t.render({}, request=None))


def custom_400_view(request, exception=None):
    return render(request, '400.html', status=400)


def robots_txt_view(request):
    """
    Standard robots.txt allowing public search crawling and protecting private sections.
    """
    site_url = getattr(settings, 'SITE_URL', 'https://chimyon-bozor.uz').rstrip('/')
    if request:
        scheme = 'https' if request.is_secure() else 'http'
        host = request.get_host()
        if host and 'localhost' not in host and '127.0.0.1' not in host:
            site_url = f"{scheme}://{host}"

    lines = [
        "User-agent: *",
        "Allow: /",
        "Allow: /categories/",
        "Allow: /products/all/",
        "Allow: /category-filter/",
        "Allow: /products/category/",
        "Allow: /product-detail/",
        "Allow: /static/",
        "Allow: /media/",
        "",
        "# Private & Transactional Paths (Disallowed from Crawling)",
        "Disallow: /dashboard/",
        "Disallow: /profile/",
        "Disallow: /cart/",
        "Disallow: /checkout/",
        "Disallow: /orders/",
        "Disallow: /payment/",
        "Disallow: /api/",
        "Disallow: /admin/",
        "Disallow: /login/",
        "Disallow: /register/",
        "Disallow: /verify-otp/",
        "Disallow: /resend-otp/",
        "Disallow: /logout/",
        "Disallow: /wishlist/",
        "",
        f"Sitemap: {site_url}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


def sitemap_xml_view(request):
    """
    Dynamic XML sitemap including only active public pages, categories, and products.
    """
    site_url = getattr(settings, 'SITE_URL', 'https://chimyon-bozor.uz').rstrip('/')
    if request:
        scheme = 'https' if request.is_secure() else 'http'
        host = request.get_host()
        if host and 'localhost' not in host and '127.0.0.1' not in host:
            site_url = f"{scheme}://{host}"

    categories = models.Category.objects.filter(is_active=True).order_by('name')
    products = models.Product.objects.filter(category__is_active=True).select_related('category').order_by('-id')

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        '  <!-- Static Core Pages -->',
        '  <url>',
        f'    <loc>{site_url}/</loc>',
        '    <changefreq>daily</changefreq>',
        '    <priority>1.0</priority>',
        '  </url>',
        '  <url>',
        f'    <loc>{site_url}/categories/</loc>',
        '    <changefreq>weekly</changefreq>',
        '    <priority>0.8</priority>',
        '  </url>',
        '  <url>',
        f'    <loc>{site_url}/products/all/</loc>',
        '    <changefreq>daily</changefreq>',
        '    <priority>0.9</priority>',
        '  </url>',
    ]

    xml_lines.append('  <!-- Active Categories -->')
    for cat in categories:
        cat_url = f"{site_url}/category-filter/{cat.id}/"
        xml_lines.extend([
            '  <url>',
            f'    <loc>{cat_url}</loc>',
            '    <changefreq>daily</changefreq>',
            '    <priority>0.8</priority>',
            '  </url>',
        ])

    xml_lines.append('  <!-- Active Public Products -->')
    for prod in products:
        prod_url = f"{site_url}/product-detail/{prod.code}/"
        lastmod = prod.updated_at.strftime('%Y-%m-%d') if prod.updated_at else (prod.created_at.strftime('%Y-%m-%d') if prod.created_at else None)
        xml_lines.append('  <url>')
        xml_lines.append(f'    <loc>{prod_url}</loc>')
        if lastmod:
            xml_lines.append(f'    <lastmod>{lastmod}</lastmod>')
        xml_lines.append('    <changefreq>daily</changefreq>')
        xml_lines.append('    <priority>0.7</priority>')
        xml_lines.append('  </url>')

    xml_lines.append('</urlset>')
    xml_content = '\n'.join(xml_lines)
    return HttpResponse(xml_content, content_type="application/xml; charset=utf-8")



