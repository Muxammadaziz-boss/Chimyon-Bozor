from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.middleware.csrf import get_token, rotate_token
from django.test import TestCase, Client
from django.urls import reverse

from main import models

User = get_user_model()


class FrontendAndDashboardLogoutCSRFTests(TestCase):
    """
    Comprehensive test suite verifying:
    1. Robust CSRF protection on /logout/ and /dashboard/logout/
    2. Zero 403 CSRF false positives with valid token
    3. Proper 403 rejection on tampered/invalid CSRF tokens
    4. HTTP method restrictions (GET rejected with 405, POST required)
    5. Session cleanup upon logout (sessionid vs dashboard_sessionid)
    6. Complete session isolation during logout flows
    7. Anonymous user logout safety (no 500s)
    8. Stale/rotated CSRF token resilience
    """

    def setUp(self):
        # Create standard customer user
        self.customer = User.objects.create_user(
            username='logout_customer',
            password='CustomerPassword123!',
            phone='+998901234567',
            phone_verified=True,
            is_active=True,
            is_staff=False
        )

        # Create staff admin user
        self.admin_user = User.objects.create_user(
            username='logout_admin',
            password='AdminPassword123!',
            phone='+998909876543',
            phone_verified=True,
            is_active=True,
            is_staff=True,
            is_superuser=True
        )

    # -------------------------------------------------------------------------
    # Scenario A: Anonymous User Logout
    # -------------------------------------------------------------------------
    def test_scenario_a_anonymous_user_logout_is_safe_and_no_500(self):
        """Anonymous user posting to /logout/ should safely redirect to index without 500 errors."""
        client = Client(enforce_csrf_checks=True)
        # Fetch initial page to obtain a valid CSRF cookie
        get_res = client.get(reverse('index'))
        csrf_token = client.cookies['csrftoken'].value

        response = client.post(reverse('logout'), {
            'csrfmiddlewaretoken': csrf_token
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('index'))

    # -------------------------------------------------------------------------
    # Scenario B: Frontend User Login -> Logout Flow
    # -------------------------------------------------------------------------
    def test_scenario_b_frontend_user_login_and_logout_flow(self):
        """Frontend user logs in, then logs out with valid CSRF token."""
        client = Client(enforce_csrf_checks=True)
        
        # 1. Load login page to establish initial CSRF cookie
        get_login = client.get(reverse('login'))
        self.assertEqual(get_login.status_code, 200)
        csrf_token = client.cookies['csrftoken'].value

        # Login
        login_res = client.post(reverse('login'), {
            'username': 'logout_customer',
            'password': 'CustomerPassword123!',
            'csrfmiddlewaretoken': csrf_token
        })
        self.assertEqual(login_res.status_code, 302)
        self.assertIn('sessionid', client.cookies)

        # Verify authenticated on profile
        profile_res = client.get(reverse('profile'))
        self.assertEqual(profile_res.status_code, 200)
        self.assertEqual(profile_res.context['request'].user, self.customer)

        # 2. Logout with fresh CSRF token from cookie
        active_csrf = client.cookies['csrftoken'].value
        logout_res = client.post(reverse('logout'), {
            'csrfmiddlewaretoken': active_csrf
        })
        self.assertEqual(logout_res.status_code, 302)
        self.assertEqual(logout_res.url, reverse('index'))

        # 3. Verify user is now logged out on frontend
        profile_after = client.get(reverse('profile'))
        self.assertEqual(profile_after.status_code, 302)
        self.assertIn('login', profile_after.url.lower())

    # -------------------------------------------------------------------------
    # Scenario C & D: Parallel Login -> Frontend Logout preserves Dashboard
    # -------------------------------------------------------------------------
    def test_scenario_c_and_d_frontend_logout_preserves_dashboard_session(self):
        """When both admin and customer are logged in, logging out frontend does NOT impact dashboard."""
        client = Client(enforce_csrf_checks=True)

        # Load d_login page to get CSRF
        client.get(reverse('d_login'))
        d_csrf = client.cookies['csrftoken'].value

        # Login to dashboard
        client.post(reverse('d_login'), {
            'username': 'logout_admin',
            'password': 'AdminPassword123!',
            'csrfmiddlewaretoken': d_csrf
        })

        # Load frontend login page to get current CSRF
        client.get(reverse('login'))
        f_csrf = client.cookies['csrftoken'].value

        # Login to frontend
        client.post(reverse('login'), {
            'username': 'logout_customer',
            'password': 'CustomerPassword123!',
            'csrfmiddlewaretoken': f_csrf
        })

        # Both cookies are active
        self.assertIn('dashboard_sessionid', client.cookies)
        self.assertIn('sessionid', client.cookies)

        # Logout from frontend
        logout_csrf = client.cookies['csrftoken'].value
        logout_res = client.post(reverse('logout'), {
            'csrfmiddlewaretoken': logout_csrf
        })
        self.assertEqual(logout_res.status_code, 302)

        # Frontend is logged out
        front_res = client.get(reverse('profile'))
        self.assertEqual(front_res.status_code, 302)

        # Dashboard is STILL fully authenticated as admin
        dash_res = client.get(reverse('d_index'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context['request'].user, self.admin_user)

    # -------------------------------------------------------------------------
    # Scenario E: Dashboard Logout preserves Frontend Session
    # -------------------------------------------------------------------------
    def test_scenario_e_dashboard_logout_preserves_frontend_session(self):
        """When both are logged in, logging out dashboard does NOT impact frontend session."""
        client = Client(enforce_csrf_checks=True)

        # 1. Login to dashboard
        client.get(reverse('d_login'))
        d_csrf = client.cookies['csrftoken'].value
        client.post(reverse('d_login'), {
            'username': 'logout_admin',
            'password': 'AdminPassword123!',
            'csrfmiddlewaretoken': d_csrf
        })

        # 2. Login to frontend
        client.get(reverse('login'))
        f_csrf = client.cookies['csrftoken'].value
        client.post(reverse('login'), {
            'username': 'logout_customer',
            'password': 'CustomerPassword123!',
            'csrfmiddlewaretoken': f_csrf
        })

        # 3. Logout from Dashboard
        dash_logout_csrf = client.cookies['csrftoken'].value
        logout_dash = client.post(reverse('d_logout'), {
            'csrfmiddlewaretoken': dash_logout_csrf
        })
        self.assertEqual(logout_dash.status_code, 302)

        # Dashboard is logged out
        dash_res = client.get(reverse('d_index'))
        self.assertEqual(dash_res.status_code, 302)

        # Frontend is STILL authenticated as customer
        front_res = client.get(reverse('profile'))
        self.assertEqual(front_res.status_code, 200)
        self.assertEqual(front_res.context['request'].user, self.customer)

    # -------------------------------------------------------------------------
    # Scenario F & G: Stale CSRF Token / Post-Login Token Rotation Handling
    # -------------------------------------------------------------------------
    def test_scenario_f_and_g_logout_with_current_rotated_token_succeeds(self):
        """Verifies that token rotated during login properly authenticates logout."""
        client = Client(enforce_csrf_checks=True)

        # 1. Anonymous user loads login page -> gets initial CSRF cookie
        res1 = client.get(reverse('login'))
        initial_csrf = client.cookies['csrftoken'].value

        # 2. User logs in -> Django rotates CSRF token!
        login_res = client.post(reverse('login'), {
            'username': 'logout_customer',
            'password': 'CustomerPassword123!',
            'csrfmiddlewaretoken': initial_csrf
        })
        self.assertEqual(login_res.status_code, 302)

        # The cookie now contains the rotated/new CSRF token
        rotated_csrf = client.cookies['csrftoken'].value
        self.assertTrue(len(rotated_csrf) > 0)

        # 3. Navigates / refreshes page
        home_res = client.get(reverse('index'))
        self.assertEqual(home_res.status_code, 200)

        # 4. Logout using the updated/synced token
        logout_res = client.post(reverse('logout'), {
            'csrfmiddlewaretoken': rotated_csrf
        })
        self.assertEqual(logout_res.status_code, 302)
        self.assertEqual(logout_res.url, reverse('index'))

    # -------------------------------------------------------------------------
    # Scenario H: Deliberately Invalid CSRF Token is Rejected (403 Forbidden)
    # -------------------------------------------------------------------------
    def test_scenario_h_invalid_csrf_token_on_logout_is_rejected_with_403(self):
        """Tampered or invalid CSRF token MUST return 403 Forbidden without bypassing CSRF."""
        client = Client(enforce_csrf_checks=True)

        # Login customer
        client.get(reverse('login'))
        csrf_token = client.cookies['csrftoken'].value
        client.post(reverse('login'), {
            'username': 'logout_customer',
            'password': 'CustomerPassword123!',
            'csrfmiddlewaretoken': csrf_token
        })

        # Submit logout with forged/invalid CSRF token
        response = client.post(reverse('logout'), {
            'csrfmiddlewaretoken': 'FORGED_INVALID_CSRF_TOKEN_ATTACK'
        })
        self.assertEqual(response.status_code, 403)

        # User remains logged in because request was rejected
        profile_res = client.get(reverse('profile'))
        self.assertEqual(profile_res.status_code, 200)
        self.assertEqual(profile_res.context['request'].user, self.customer)

    # -------------------------------------------------------------------------
    # Scenario I: GET /logout/ Method Not Allowed (No State Change on GET)
    # -------------------------------------------------------------------------
    def test_scenario_i_get_logout_returns_405_and_preserves_session(self):
        """GET request to /logout/ must not log out user and should return 405 Method Not Allowed."""
        client = Client(enforce_csrf_checks=True)
        client.get(reverse('login'))
        csrf_token = client.cookies['csrftoken'].value
        client.post(reverse('login'), {
            'username': 'logout_customer',
            'password': 'CustomerPassword123!',
            'csrfmiddlewaretoken': csrf_token
        })

        # Send GET request to /logout/
        response = client.get(reverse('logout'))
        self.assertEqual(response.status_code, 405)

        # User is still logged in
        profile_res = client.get(reverse('profile'))
        self.assertEqual(profile_res.status_code, 200)

    # -------------------------------------------------------------------------
    # Scenario J: Valid POST /logout/ with CSRF Header / Token
    # -------------------------------------------------------------------------
    def test_scenario_j_valid_post_logout_via_header_or_post(self):
        """AJAX / fetch logout with HTTP_X_CSRFTOKEN header successfully logs out."""
        client = Client(enforce_csrf_checks=True)
        client.get(reverse('login'))
        csrf_token = client.cookies['csrftoken'].value
        client.post(reverse('login'), {
            'username': 'logout_customer',
            'password': 'CustomerPassword123!',
            'csrfmiddlewaretoken': csrf_token
        })

        active_csrf = client.cookies['csrftoken'].value
        response = client.post(reverse('logout'), HTTP_X_CSRFTOKEN=active_csrf)
        self.assertEqual(response.status_code, 302)

        # User is logged out
        profile_res = client.get(reverse('profile'))
        self.assertEqual(profile_res.status_code, 302)
