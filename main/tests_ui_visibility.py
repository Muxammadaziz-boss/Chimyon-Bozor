from django.test import TestCase, Client
from django.urls import reverse
from decimal import Decimal
from main import models


class UIVisibilityAndInteractionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = models.User.objects.create_user(
            username='uitestuser',
            password='password123',
            is_staff=True,
            is_superuser=True
        )
        self.category = models.Category.objects.create(name="Elektronika", is_active=True)
        self.product = models.Product.objects.create(
            category=self.category,
            name="Test Phone Pro",
            description="High quality phone",
            price=Decimal('1200000.00'),
            count=15
        )

    # 1. Base template has proper toast pointer-events and visibility controls
    def test_toast_pointer_events_and_visibility(self):
        res = self.client.get(reverse('index'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn("toastEl.style.pointerEvents = 'none'", content)
        self.assertIn("toastEl.style.visibility = 'hidden'", content)
        self.assertIn("toastEl.style.pointerEvents = 'auto'", content)
        self.assertIn("toastEl.style.visibility = 'visible'", content)

    # 2. Base template has color fallback for gradient text
    def test_logo_gradient_color_fallback(self):
        res = self.client.get(reverse('index'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn("color: #7C3AED; background: linear-gradient", content)

    # 3. Optimistic UI contains error rollback handlers
    def test_optimistic_ui_rollback_present(self):
        res = self.client.get(reverse('index'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn("updateBadge('.cart-count-badge', -1)", content)
        self.assertIn("updateBadge('.wishlist-count-badge', -1)", content)
        self.assertIn("Server bilan bog\\'lanishda xatolik yuz berdi!", content)

    # 4. Cart quantity update has rollback data attributes and fallback
    def test_cart_quantity_update_rollback(self):
        self.client.login(username='uitestuser', password='password123')
        res = self.client.get(reverse('cart'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn("data-last-valid-qty", content)
        self.assertIn("qtyInput.value = previousQty", content)

    # 5. Dashboard warning cards use high-contrast icon styling
    def test_dashboard_warning_card_contrast(self):
        self.client.login(username='uitestuser', password='password123')
        
        res_index = self.client.get(reverse('d_index'))
        self.assertEqual(res_index.status_code, 200)
        self.assertIn("style=\"color: #78350f !important;\"", res_index.content.decode('utf-8'))

        res_inventory = self.client.get(reverse('d_inventory'))
        self.assertEqual(res_inventory.status_code, 200)
        self.assertIn("style=\"color: #78350f !important;\"", res_inventory.content.decode('utf-8'))

        res_analytics = self.client.get(reverse('d_analytics'))
        self.assertEqual(res_analytics.status_code, 200)
        self.assertIn("style=\"color: #78350f !important;\"", res_analytics.content.decode('utf-8'))

    # 6. Dashboard navbar renders admin username
    def test_dashboard_admin_username_displayed(self):
        self.client.login(username='uitestuser', password='password123')
        res = self.client.get(reverse('d_index'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn("uitestuser", content)

    # 7. Category filter product count is visible on all screen sizes
    def test_category_filter_mobile_product_count_visibility(self):
        res = self.client.get(reverse('all_products'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn("Topildi: <strong class=\"text-primary\">", content)
        self.assertNotIn("d-none d-sm-inline\">\n                    Topildi:", content)

    # 8. Dashboard order list active tab has proper white icon and badge contrast
    def test_order_list_active_tab_contrast(self):
        self.client.login(username='uitestuser', password='password123')
        res = self.client.get(reverse('d_orders') + '?status=2')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn("color: #ffffff;", content)
        self.assertIn("badge-white text-primary font-weight-bold", content)

    # 9. Custom select options have pointer-events protection
    def test_custom_select_pointer_events_protection(self):
        self.client.login(username='uitestuser', password='password123')
        res_profile = self.client.get(reverse('profile'))
        self.assertEqual(res_profile.status_code, 200)
        content = res_profile.content.decode('utf-8')
        self.assertIn("pointer-events: none;", content)
        self.assertIn("pointer-events: auto;", content)

    # 10. Dashboard inventory stock input width handles large numbers
    def test_dashboard_inventory_input_width(self):
        self.client.login(username='uitestuser', password='password123')
        res = self.client.get(reverse('d_inventory'))
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn("width: 95px; min-width: 95px;", content)
