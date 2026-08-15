from django.test import TestCase, Client
from django.urls import reverse
from decimal import Decimal
from main import models
from main.context_processors import site_settings


class CategoryNavigationAndCatalogTests(TestCase):
    def setUp(self):
        self.client = Client()
        
        # Create 15 categories to test limit
        self.categories = []
        for i in range(1, 16):
            cat = models.Category.objects.create(
                name=f"Kategoriya {i:02d}",
                is_active=True
            )
            self.categories.append(cat)
            
            # Add products to some categories
            if i <= 5:
                for p_idx in range(i * 2):
                    models.Product.objects.create(
                        category=cat,
                        name=f"Mahsulot {i}_{p_idx}",
                        description="Test tavsif",
                        price=Decimal('10000.00'),
                        count=10
                    )

        # Create 2 inactive categories
        self.inactive_cat1 = models.Category.objects.create(name="Yashirin Kategoriya 1", is_active=False)
        self.inactive_cat2 = models.Category.objects.create(name="Yashirin Kategoriya 2", is_active=False)

    # 1. Header Dropdown limits to max 9 categories
    def test_header_dropdown_limits_to_max_9_categories(self):
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        nav_categories = response.context['nav_categories']
        self.assertLessEqual(len(nav_categories), 9)
        active_total = models.Category.objects.filter(is_active=True).count()
        self.assertEqual(response.context['total_categories_count'], active_total)

    # 2. Inactive categories are excluded from nav and categories page
    def test_inactive_categories_are_excluded(self):
        response = self.client.get(reverse('categories'))
        self.assertEqual(response.status_code, 200)
        category_names = [c.name for c in response.context['categories']]
        self.assertNotIn("Yashirin Kategoriya 1", category_names)
        self.assertNotIn("Yashirin Kategoriya 2", category_names)

    # 3. Categories page loads on /categories/ and /all-categories/
    def test_categories_page_urls_load_successfully(self):
        res1 = self.client.get(reverse('categories'))
        self.assertEqual(res1.status_code, 200)
        self.assertTemplateUsed(res1, 'front/categories.html')
        self.assertContains(res1, "Mahsulot Kategoriyalari")
        self.assertContains(res1, "Kerakli mahsulotni kategoriya bo‘yicha tez toping.")

        res2 = self.client.get(reverse('all_categories'))
        self.assertEqual(res2.status_code, 200)

    # 4. Search filtering on categories page
    def test_categories_page_search_filtering(self):
        named_cat = models.Category.objects.create(name="Noyob Smartfonlar", is_active=True)
        
        response = self.client.get(reverse('categories'), {'q': 'Smartfonlar'})
        self.assertEqual(response.status_code, 200)
        matched_cats = response.context['categories']
        self.assertEqual(len(matched_cats), 1)
        self.assertEqual(matched_cats[0].id, named_cat.id)

    # 5. Product counts accuracy
    def test_product_counts_accuracy(self):
        cat5 = self.categories[4]  # index 4 is Kategoriya 05 with 5*2 = 10 products
        response = self.client.get(reverse('categories'))
        self.assertEqual(response.status_code, 200)
        
        cat5_in_context = next(c for c in response.context['categories'] if c.id == cat5.id)
        self.assertEqual(cat5_in_context.product_count, 10)

    # 6. Category Links Route Correctly
    def test_category_links_route_correctly(self):
        target_cat = self.categories[0]
        url = reverse('category_filter', kwargs={'category_id': target_cat.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_category'], target_cat.id)

    # 7. Empty Search Result State
    def test_empty_search_result_state(self):
        response = self.client.get(reverse('categories'), {'q': 'Mavjud_Bolmagan_Qidiruv_12345'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['categories']), 0)
        self.assertEqual(response.context['total_categories'], 0)

    # 8. Grouping structure formation
    def test_grouping_structure(self):
        models.Category.objects.create(name="Erkaklar Kiyimlari", is_active=True)
        models.Category.objects.create(name="Smartfonlar va Gadjetlar", is_active=True)
        models.Category.objects.create(name="Oshxona Anjomlari", is_active=True)

        response = self.client.get(reverse('categories'))
        self.assertEqual(response.status_code, 200)
        grouped = response.context['grouped_categories']
        group_ids = [g['id'] for g in grouped]
        
        self.assertIn('clothing', group_ids)
        self.assertIn('tech', group_ids)
        self.assertIn('home', group_ids)
