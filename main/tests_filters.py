from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from main import models


class ProductFilterAndSortingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = models.User.objects.create_user(username='testreviewer', password='password123')

        self.cat_phones = models.Category.objects.create(name="Smartfonlar", is_active=True)
        self.cat_clothes = models.Category.objects.create(name="Kiyimlar", is_active=True)

        # Product 1: Phone, 500,000 UZS, no discount, stock=10
        self.p1 = models.Product.objects.create(
            category=self.cat_phones,
            name="iPhone 13",
            description="Apple smartfoni",
            price=Decimal('500000.00'),
            count=10,
            discount_status=False
        )

        # Product 2: Phone, 1,000,000 UZS, 20% discount (800,000 UZS), stock=3 (low stock)
        self.p2 = models.Product.objects.create(
            category=self.cat_phones,
            name="Samsung Galaxy S22",
            description="Samsung flagmani",
            price=Decimal('1000000.00'),
            discount_price=Decimal('800000.00'),
            discount_status=True,
            count=3
        )

        # Product 3: Phone, 200,000 UZS, 50% discount (100,000 UZS), stock=0 (out of stock)
        self.p3 = models.Product.objects.create(
            category=self.cat_phones,
            name="Redmi Note 10",
            description="Xiaomi byudjet smartfoni",
            price=Decimal('200000.00'),
            discount_price=Decimal('100000.00'),
            discount_status=True,
            count=0
        )

        # Product 4: Clothes, 150,000 UZS, stock=20
        self.p4 = models.Product.objects.create(
            category=self.cat_clothes,
            name="Erkaklar ko'ylagi",
            description="Paxta matoli ko'ylak",
            price=Decimal('150000.00'),
            count=20,
            discount_status=False
        )

        # Reviews: Give p1 average rating 5.0, and p2 average rating 3.0
        models.Review.objects.create(user=self.user, product=self.p1, rating=5, text="Ajoyib!")
        models.Review.objects.create(user=self.user, product=self.p2, rating=3, text="O'rtacha")

    # 1. Category Filter
    def test_category_filter(self):
        url = reverse('all_products')
        res = self.client.get(url, {'category': self.cat_phones.id})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p1.id, product_ids)
        self.assertIn(self.p2.id, product_ids)
        self.assertNotIn(self.p4.id, product_ids)

    # 2. Min Price Filter
    def test_price_min_filter(self):
        url = reverse('all_products')
        res = self.client.get(url, {'min_price': '400000'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p1.id, product_ids)  # 500k
        self.assertIn(self.p2.id, product_ids)  # effective 800k
        self.assertNotIn(self.p3.id, product_ids) # effective 100k
        self.assertNotIn(self.p4.id, product_ids) # 150k

    # 3. Max Price Filter
    def test_price_max_filter(self):
        url = reverse('all_products')
        res = self.client.get(url, {'max_price': '200000'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p3.id, product_ids) # effective 100k
        self.assertIn(self.p4.id, product_ids) # 150k
        self.assertNotIn(self.p1.id, product_ids) # 500k
        self.assertNotIn(self.p2.id, product_ids) # effective 800k

    # 4. Price Range Filter
    def test_price_range_filter(self):
        url = reverse('all_products')
        res = self.client.get(url, {'min_price': '120000', 'max_price': '600000'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p1.id, product_ids) # 500k
        self.assertIn(self.p4.id, product_ids) # 150k
        self.assertNotIn(self.p2.id, product_ids) # 800k
        self.assertNotIn(self.p3.id, product_ids) # 100k

    # 5. Discount Only Filter
    def test_discount_only_filter(self):
        url = reverse('all_products')
        res = self.client.get(url, {'discount': '1'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p2.id, product_ids) # discounted
        self.assertIn(self.p3.id, product_ids) # discounted
        self.assertNotIn(self.p1.id, product_ids) # not discounted
        self.assertNotIn(self.p4.id, product_ids) # not discounted

    # 6. Discount Percent Filter (20%+)
    def test_discount_20_percent_filter(self):
        url = reverse('all_products')
        res = self.client.get(url, {'discount': '20'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p2.id, product_ids) # 20%
        self.assertIn(self.p3.id, product_ids) # 50%
        self.assertNotIn(self.p1.id, product_ids)

    # 7. Stock In Stock Filter
    def test_stock_in_stock_filter(self):
        url = reverse('all_products')
        res = self.client.get(url, {'stock': 'in_stock'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p1.id, product_ids)
        self.assertIn(self.p2.id, product_ids)
        self.assertIn(self.p4.id, product_ids)
        self.assertNotIn(self.p3.id, product_ids) # count=0

    # 8. Stock Low Stock Filter (1-5)
    def test_stock_low_stock_filter(self):
        url = reverse('all_products')
        res = self.client.get(url, {'stock': 'low_stock'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p2.id, product_ids) # count=3
        self.assertNotIn(self.p1.id, product_ids) # count=10
        self.assertNotIn(self.p3.id, product_ids) # count=0

    # 9. Stock Out of Stock Filter
    def test_stock_out_of_stock_filter(self):
        url = reverse('all_products')
        res = self.client.get(url, {'stock': 'out_of_stock'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p3.id, product_ids) # count=0
        self.assertNotIn(self.p1.id, product_ids)

    # 10. Rating Filter (4+ stars)
    def test_rating_filter_4_stars(self):
        url = reverse('all_products')
        res = self.client.get(url, {'rating': '4'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p1.id, product_ids) # rating 5.0
        self.assertNotIn(self.p2.id, product_ids) # rating 3.0

    # 11. Sorting Price Ascending
    def test_sorting_price_asc(self):
        url = reverse('all_products')
        res = self.client.get(url, {'category': self.cat_phones.id, 'sort': 'price_asc'})
        self.assertEqual(res.status_code, 200)
        products = list(res.context['products'])
        # Effective prices: p3 (100k) < p1 (500k) < p2 (800k)
        self.assertEqual(products[0].id, self.p3.id)
        self.assertEqual(products[1].id, self.p1.id)
        self.assertEqual(products[2].id, self.p2.id)

    # 12. Sorting Price Descending
    def test_sorting_price_desc(self):
        url = reverse('all_products')
        res = self.client.get(url, {'category': self.cat_phones.id, 'sort': 'price_desc'})
        self.assertEqual(res.status_code, 200)
        products = list(res.context['products'])
        # Effective prices: p2 (800k) > p1 (500k) > p3 (100k)
        self.assertEqual(products[0].id, self.p2.id)
        self.assertEqual(products[1].id, self.p1.id)
        self.assertEqual(products[2].id, self.p3.id)

    # 13. Search + Filter Combined
    def test_search_plus_filter_combined(self):
        url = reverse('all_products')
        res = self.client.get(url, {'q': 'iPhone', 'category': self.cat_phones.id, 'min_price': '400000'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertEqual(len(product_ids), 1)
        self.assertIn(self.p1.id, product_ids)

    # 14. Active Chips Population
    def test_active_chips_population(self):
        url = reverse('all_products')
        res = self.client.get(url, {'q': 'Samsung', 'min_price': '100000', 'discount': '20', 'stock': 'in_stock'})
        self.assertEqual(res.status_code, 200)
        chips = res.context['active_chips']
        self.assertEqual(len(chips), 4)
        chip_types = [c['type'] for c in chips]
        self.assertIn('q', chip_types)
        self.assertIn('price', chip_types)
        self.assertIn('discount', chip_types)
        self.assertIn('stock', chip_types)

    # 15. Invalid Price Handled Gracefully
    def test_invalid_price_handled_gracefully(self):
        url = reverse('all_products')
        res = self.client.get(url, {'min_price': 'invalid_text', 'max_price': '-500'})
        self.assertEqual(res.status_code, 200)

    # 16. Min Greater Than Max Swapped
    def test_min_greater_than_max_swapped(self):
        url = reverse('all_products')
        res = self.client.get(url, {'min_price': '900000', 'max_price': '100000'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['min_price'], Decimal('100000'))
        self.assertEqual(res.context['max_price'], Decimal('900000'))

    # 17. No Results State
    def test_no_results_state(self):
        url = reverse('all_products')
        res = self.client.get(url, {'q': 'NonExistentProductXYZ123'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.context['products']), 0)
        self.assertEqual(res.context['total_products'], 0)

    # 18. Category Filter View URL (/category-filter/<id>/)
    def test_category_filter_view_url(self):
        url = reverse('category_filter', kwargs={'category_id': self.cat_clothes.id})
        res = self.client.get(url, {'min_price': '100000'})
        self.assertEqual(res.status_code, 200)
        product_ids = [p.id for p in res.context['products']]
        self.assertIn(self.p4.id, product_ids)
        self.assertNotIn(self.p1.id, product_ids)

    # 19. Category Count Consistency in Sidebar
    def test_category_count_consistency_in_sidebar(self):
        url = reverse('all_products')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        sidebar_cats = {c.id: c.product_count for c in res.context['categories']}
        self.assertEqual(sidebar_cats[self.cat_phones.id], 3)
        self.assertEqual(sidebar_cats[self.cat_clothes.id], 1)

    # 20. Pagination Preserves Filters
    def test_pagination_preserves_filters(self):
        # Create 22 more phone products to trigger pagination (>20 per page)
        for i in range(22):
            models.Product.objects.create(
                category=self.cat_phones,
                name=f"Qo'shimcha Phone {i}",
                description="desc",
                price=Decimal('500000.00'),
                count=10
            )

        url = reverse('all_products')
        res = self.client.get(url, {'category': self.cat_phones.id, 'page': '2'})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context['page_obj'].has_previous())
        self.assertContains(res, f"category={self.cat_phones.id}")

    # 21. Filter Panel UI Elements (Contrast, Badges, Presets, Header)
    def test_filter_panel_ui_elements(self):
        url = reverse('all_products')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        # Price input contrast styling
        self.assertIn("class=\"price-input-control\"", content)
        self.assertIn("placeholder=\"1 000 000\"", content)

        # Price presets formatted with spaces / Uzbek words
        self.assertIn("0 – 100 ming", content)
        self.assertIn("100 – 500 ming", content)
        self.assertIn("500 ming – 1 mln", content)
        self.assertIn("1 mln+", content)

        # Category badge suffix 'ta'
        self.assertIn("class=\"filter-cat-badge\">3 ta</span>", content)
        self.assertIn("class=\"filter-cat-badge\">1 ta</span>", content)

        # All categories header link
        self.assertIn("class=\"filter-all-cats-btn\"", content)
        self.assertIn("Barchasi <i class=\"fas fa-arrow-right", content)

    # 22. Price Sanitization Against Non-Numeric Chars (e.g. 'e342', '2143e', '3r2341234eq')
    def test_price_input_sanitization_against_invalid_chars(self):
        url = reverse('all_products')

        # 1. Mixed letters and digits
        res = self.client.get(url, {'min_price': '3r2341234eq', 'max_price': '1a000b000'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['min_price_raw'], '32341234')
        self.assertEqual(res.context['max_price_raw'], '1000000')

        # 2. Exponential chars and symbols: e342, 2143e, 1.5, +123, -500
        res2 = self.client.get(url, {'min_price': 'e342', 'max_price': '2143e'})
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.context['min_price_raw'], '342')
        self.assertEqual(res2.context['max_price_raw'], '2143')

        res3 = self.client.get(url, {'min_price': '1.5', 'max_price': '+123'})
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(res3.context['min_price_raw'], '15')
        self.assertEqual(res3.context['max_price_raw'], '123')

        res4 = self.client.get(url, {'min_price': '-500', 'max_price': '1000'})
        self.assertEqual(res4.status_code, 200)
        self.assertEqual(res4.context['min_price_raw'], '500')
        self.assertEqual(res4.context['max_price_raw'], '1000')

        # 3. Completely non-numeric
        res_empty = self.client.get(url, {'min_price': 'abc', 'max_price': 'xyz'})
        self.assertEqual(res_empty.status_code, 200)
        self.assertEqual(res_empty.context['min_price_raw'], '')
        self.assertEqual(res_empty.context['max_price_raw'], '')

        # 4. Template attributes check (inputmode numeric, inline oninput fallback, sanitize function)
        content = res.content.decode('utf-8')
        self.assertIn('inputmode="numeric"', content)
        self.assertIn('pattern="[0-9]*"', content)
        self.assertIn("oninput=\"this.value=this.value.replace(/\\D/g,'')\"", content)
        self.assertIn("function sanitizeNumericPriceInput", content)



