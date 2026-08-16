from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from main import models

User = get_user_model()


class DashboardFrontendSessionIsolationTests(TestCase):
    """
    Tests complete Session & Authentication Isolation between Dashboard and Frontend.
    Ensures that Dashboard ('dashboard_sessionid') and Frontend ('sessionid') operate
    independently without session leakage, fixation, or overwriting.
    """

    def setUp(self):
        # 1. Create a regular customer user
        self.regular_user = User.objects.create_user(
            username='regular_customer',
            password='CustomerPass123!',
            phone='+998901112233',
            phone_verified=True,
            is_active=True,
            is_staff=False
        )

        # 2. Create an admin/staff user
        self.admin_user = User.objects.create_user(
            username='dashboard_admin',
            password='AdminPass123!',
            phone='+998909998877',
            phone_verified=True,
            is_active=True,
            is_staff=True,
            is_superuser=True
        )

        self.category = models.Category.objects.create(name="Mebel", is_active=True)
        self.product = models.Product.objects.create(
            name="Stol",
            category=self.category,
            price=Decimal('150000.00'),
            count=10
        )

    # -------------------------------------------------------------------------
    # TEST 1: Dashboard admin login -> Dashboard = admin, Frontend = independent
    # -------------------------------------------------------------------------
    def test_01_dashboard_admin_login_sets_isolated_dashboard_cookie(self):
        client = Client()
        
        # Login to dashboard
        login_res = client.post(reverse('d_login'), {
            'username': 'dashboard_admin',
            'password': 'AdminPass123!'
        })
        self.assertEqual(login_res.status_code, 302)
        
        # Verify dashboard_sessionid cookie was set
        self.assertIn('dashboard_sessionid', client.cookies)
        dashboard_cookie = client.cookies['dashboard_sessionid'].value
        self.assertTrue(len(dashboard_cookie) > 0)

        # Dashboard page is accessible
        dash_res = client.get(reverse('d_index'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context['request'].user, self.admin_user)

        # Frontend pages treat user as Anonymous (not logged into frontend sessionid)
        front_res = client.get(reverse('profile'))
        self.assertEqual(front_res.status_code, 302)
        self.assertIn('login', front_res.url.lower())

    # -------------------------------------------------------------------------
    # TEST 2: Frontend user login -> Frontend = user, Dashboard = independent
    # -------------------------------------------------------------------------
    def test_02_frontend_user_login_sets_isolated_frontend_cookie(self):
        client = Client()

        # Login to frontend
        login_res = client.post(reverse('login'), {
            'username': 'regular_customer',
            'password': 'CustomerPass123!'
        })
        self.assertEqual(login_res.status_code, 302)

        # Verify frontend sessionid cookie was set
        self.assertIn('sessionid', client.cookies)
        front_cookie = client.cookies['sessionid'].value
        self.assertTrue(len(front_cookie) > 0)

        # Frontend profile is accessible as regular_customer
        front_res = client.get(reverse('profile'))
        self.assertEqual(front_res.status_code, 200)
        self.assertEqual(front_res.context['request'].user, self.regular_user)

        # Dashboard is NOT accessible (no dashboard_sessionid)
        dash_res = client.get(reverse('d_index'))
        self.assertEqual(dash_res.status_code, 302)
        self.assertIn('dashboard', dash_res.url.lower())

    # -------------------------------------------------------------------------
    # TEST 3: Parallel Login Coexistence (Dashboard = Admin, Frontend = User)
    # -------------------------------------------------------------------------
    def test_03_parallel_dashboard_and_frontend_sessions_coexist(self):
        client = Client()

        # 1. Login to Dashboard as admin
        client.post(reverse('d_login'), {
            'username': 'dashboard_admin',
            'password': 'AdminPass123!'
        })

        # 2. Login to Frontend as regular_user
        client.post(reverse('login'), {
            'username': 'regular_customer',
            'password': 'CustomerPass123!'
        })

        # Both cookies exist simultaneously
        self.assertIn('dashboard_sessionid', client.cookies)
        self.assertIn('sessionid', client.cookies)

        # Dashboard view sees admin_user
        dash_res = client.get(reverse('d_index'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context['request'].user, self.admin_user)
        self.assertEqual(dash_res.context['request'].user.username, 'dashboard_admin')

        # Frontend view sees regular_customer
        front_res = client.get(reverse('profile'))
        self.assertEqual(front_res.status_code, 200)
        self.assertEqual(front_res.context['request'].user, self.regular_user)
        self.assertEqual(front_res.context['request'].user.username, 'regular_customer')

    # -------------------------------------------------------------------------
    # TEST 4: Dashboard Logout leaves Frontend Session Active
    # -------------------------------------------------------------------------
    def test_04_dashboard_logout_preserves_frontend_session(self):
        client = Client()

        # Login to both
        client.post(reverse('d_login'), {'username': 'dashboard_admin', 'password': 'AdminPass123!'})
        client.post(reverse('login'), {'username': 'regular_customer', 'password': 'CustomerPass123!'})

        # Logout from Dashboard
        logout_dash = client.post(reverse('d_logout'))
        self.assertEqual(logout_dash.status_code, 302)

        # Dashboard is now logged out
        dash_res = client.get(reverse('d_index'))
        self.assertEqual(dash_res.status_code, 302)

        # Frontend is STILL logged in as regular_customer
        front_res = client.get(reverse('profile'))
        self.assertEqual(front_res.status_code, 200)
        self.assertEqual(front_res.context['request'].user, self.regular_user)

    # -------------------------------------------------------------------------
    # TEST 5: Frontend Logout leaves Dashboard Session Active
    # -------------------------------------------------------------------------
    def test_05_frontend_logout_preserves_dashboard_session(self):
        client = Client()

        # Login to both
        client.post(reverse('d_login'), {'username': 'dashboard_admin', 'password': 'AdminPass123!'})
        client.post(reverse('login'), {'username': 'regular_customer', 'password': 'CustomerPass123!'})

        # Logout from Frontend
        logout_front = client.post(reverse('logout'))
        self.assertEqual(logout_front.status_code, 302)

        # Frontend is now logged out
        front_res = client.get(reverse('profile'))
        self.assertEqual(front_res.status_code, 302)

        # Dashboard is STILL logged in as dashboard_admin
        dash_res = client.get(reverse('d_index'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context['request'].user, self.admin_user)

    # -------------------------------------------------------------------------
    # TEST 6: Regular Frontend User cannot access Dashboard
    # -------------------------------------------------------------------------
    def test_06_regular_user_blocked_from_dashboard_urls(self):
        client = Client()
        client.post(reverse('login'), {'username': 'regular_customer', 'password': 'CustomerPass123!'})

        # Direct access to various dashboard endpoints
        endpoints = [
            reverse('d_index'),
            reverse('d_orders'),
            reverse('d_payments'),
            reverse('d_analytics'),
            reverse('d_list_users'),
        ]
        for ep in endpoints:
            res = client.get(ep)
            self.assertEqual(res.status_code, 302, f"Endpoint {ep} must redirect unauthorized users")
            self.assertIn('login', res.url.lower())

    # -------------------------------------------------------------------------
    # TEST 7: Dashboard Admin accessing Frontend uses Frontend Context
    # -------------------------------------------------------------------------
    def test_07_dashboard_admin_on_frontend_uses_frontend_context(self):
        client = Client()
        client.post(reverse('d_login'), {'username': 'dashboard_admin', 'password': 'AdminPass123!'})

        # On frontend index, request.user is Anonymous
        res = client.get(reverse('index'))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context['request'].user.is_anonymous)

    # -------------------------------------------------------------------------
    # TEST 8: Data & Cart Isolation Under Parallel Sessions
    # -------------------------------------------------------------------------
    def test_08_user_cart_and_data_isolation(self):
        client = Client()
        # Admin in dashboard, User in frontend
        client.post(reverse('d_login'), {'username': 'dashboard_admin', 'password': 'AdminPass123!'})
        client.post(reverse('login'), {'username': 'regular_customer', 'password': 'CustomerPass123!'})

        # Add item to cart on frontend
        add_res = client.post(reverse('add_to_cart', kwargs={'product_code': self.product.code}))
        self.assertIn(add_res.status_code, [200, 302])

        # Verify the cart product belongs exclusively to regular_customer, not admin_user
        cart = models.Cart.objects.filter(user=self.regular_user, status=1).first()
        self.assertIsNotNone(cart)
        self.assertEqual(cart.cart_products.count(), 1)

        admin_cart = models.Cart.objects.filter(user=self.admin_user, status=1).first()
        self.assertIsNone(admin_cart)
