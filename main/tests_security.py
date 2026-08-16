import os
from decimal import Decimal
from io import BytesIO
from PIL import Image
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from main import models
from main.validators import validate_image_file

User = get_user_model()


class SecurityHardeningComprehensiveTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        self.csrf_client = Client(enforce_csrf_checks=True)

        # Standard test user
        self.user1 = User.objects.create_user(
            username='security_user1',
            password='TestPassword123!',
            phone='+998901112233',
            phone_verified=True,
            is_active=True
        )

        # Second test user for IDOR testing
        self.user2 = User.objects.create_user(
            username='security_user2',
            password='TestPassword123!',
            phone='+998904445566',
            phone_verified=True,
            is_active=True
        )

        # Staff user for dashboard testing
        self.staff_user = User.objects.create_user(
            username='security_admin',
            password='AdminPassword123!',
            phone='+998907778899',
            is_staff=True,
            is_active=True
        )

        self.category = models.Category.objects.create(name="Xavfsizlik Kategoriya", is_active=True)
        self.product = models.Product.objects.create(
            name="Xavfsiz Mahsulot",
            category=self.category,
            price=Decimal('250000.00'),
            count=15
        )

    # -------------------------------------------------------------
    # 1. IDOR / Object-Level Authorization Protection
    # -------------------------------------------------------------
    def test_idor_unauthorized_order_access_prevented(self):
        """User1 should not be able to view or manipulate User2's order details."""
        order_user2 = models.Cart.objects.create(user=self.user2, status=2)
        models.CartProduct.objects.create(cart=order_user2, product=self.product, count=1)

        # Login as User1 and try to access User2's order
        self.client.force_login(self.user1)
        response = self.client.get(reverse('order_detail', kwargs={'code': order_user2.code}))
        self.assertEqual(response.status_code, 404)

        # Try to access payment success page of User2
        response_success = self.client.get(reverse('payment_success', kwargs={'code': order_user2.code}))
        self.assertEqual(response_success.status_code, 404)

        # Try to pay balance for User2's order
        response_pay = self.client.post(reverse('pay_balance', kwargs={'code': order_user2.code}), {'provider': 'click'})
        self.assertEqual(response_pay.status_code, 404)

    # -------------------------------------------------------------
    # 2. Dashboard / Admin Access Control
    # -------------------------------------------------------------
    def test_unauthorized_dashboard_access_redirected(self):
        """Ordinary non-staff users cannot access dashboard views."""
        self.client.force_login(self.user1)
        response = self.client.get(reverse('d_index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url.lower())

        response_orders = self.client.get(reverse('d_orders'))
        self.assertEqual(response_orders.status_code, 302)

    def test_staff_dashboard_access_allowed(self):
        """Staff members are granted access to dashboard."""
        self.client.force_login(self.staff_user)
        response = self.client.get(reverse('d_index'))
        self.assertEqual(response.status_code, 200)

    # -------------------------------------------------------------
    # 3. Open Redirect Prevention
    # -------------------------------------------------------------
    def test_open_redirect_on_login_prevented(self):
        """Login redirects to external malicious domains are blocked."""
        response = self.client.post(reverse('login'), {
            'username': 'security_user1',
            'password': 'TestPassword123!',
            'next': 'https://attacker.evil.com/phishing'
        })
        self.assertEqual(response.status_code, 302)
        # Must redirect safely to internal index, not evil.com
        self.assertNotIn('attacker.evil.com', response.url)
        self.assertTrue(response.url == '/' or response.url == reverse('index'))

    def test_open_redirect_on_dashboard_login_prevented(self):
        """Dashboard login redirects to external malicious domains are blocked."""
        response = self.client.post(reverse('d_login'), {
            'username': 'security_admin',
            'password': 'AdminPassword123!',
            'next': 'https://attacker.evil.com/admin-harvest'
        })
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('attacker.evil.com', response.url)
        self.assertEqual(response.url, reverse('d_index'))

    # -------------------------------------------------------------
    # 4. CSRF Protection
    # -------------------------------------------------------------
    def test_csrf_protection_on_state_changing_post(self):
        """POST without CSRF token is rejected with 403 when CSRF is enforced."""
        self.csrf_client.force_login(self.user1)
        response = self.csrf_client.post(reverse('add_to_cart', kwargs={'product_code': self.product.code}))
        self.assertEqual(response.status_code, 403)

    # -------------------------------------------------------------
    # 5. File Upload Security & Validation
    # -------------------------------------------------------------
    def test_file_upload_valid_image_passes(self):
        """Valid JPEG image passes upload validation."""
        im_io = BytesIO()
        img = Image.new('RGB', (100, 100), color='blue')
        img.save(im_io, format='JPEG')
        im_io.seek(0)
        uploaded = SimpleUploadedFile("avatar.jpg", im_io.getvalue(), content_type="image/jpeg")
        
        # Should not raise exception
        validate_image_file(uploaded)

    def test_file_upload_executable_script_rejected(self):
        """PHP, Python, shell scripts disguised as images are rejected."""
        fake_file = SimpleUploadedFile("shell.php", b"<?php echo 'pwned'; ?>", content_type="application/x-php")
        with self.assertRaises(ValidationError):
            validate_image_file(fake_file)

    def test_file_upload_path_traversal_filename_rejected(self):
        """Filenames with directory traversal sequences are rejected."""
        mock_traversal = type('MockFile', (), {
            'name': '../../evil.png',
            'size': 1024,
            'seek': lambda self, *args: None
        })()
        with self.assertRaises(ValidationError):
            validate_image_file(mock_traversal)

    def test_file_upload_corrupt_or_fake_image_rejected(self):
        """Files with fake extension but corrupt content are rejected."""
        fake_jpg = SimpleUploadedFile("fake.jpg", b"NOT AN IMAGE FILE AT ALL", content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            validate_image_file(fake_jpg)

    # -------------------------------------------------------------
    # 6. OTP Rate Limiting & Cooldown
    # -------------------------------------------------------------
    def test_otp_resend_cooldown_rate_limit(self):
        """Requesting OTP resends repeatedly triggers a rate limit warning."""
        session = self.client.session
        session['otp_user_id'] = self.user1.id
        session['otp_phone'] = self.user1.phone
        session.save()

        # 1st resend succeeds
        res1 = self.client.get(reverse('resend_otp'))
        self.assertEqual(res1.status_code, 302)

        # 2nd immediate resend within 60s is rate-limited
        res2 = self.client.get(reverse('resend_otp'))
        self.assertEqual(res2.status_code, 302)
        # User is returned to verify_otp with cooldown message
        messages = list(res2.wsgi_request._messages)
        self.assertTrue(any("kuting" in str(m) for m in messages))

    # -------------------------------------------------------------
    # 7. Cookie & Session Security Settings
    # -------------------------------------------------------------
    def test_session_and_cookie_security_configuration(self):
        """Session cookies must be HttpOnly and SameSite=Lax."""
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, 'Lax')
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, 'Lax')
        self.assertTrue(settings.SECURE_CONTENT_TYPE_NOSNIFF)
        self.assertEqual(settings.X_FRAME_OPTIONS, 'DENY')
        self.assertEqual(settings.SECURE_REFERRER_POLICY, 'strict-origin-when-cross-origin')

    # -------------------------------------------------------------
    # 8. Security Headers Middleware
    # -------------------------------------------------------------
    def test_security_headers_in_response(self):
        """Responses must include X-Content-Type-Options, Permissions-Policy, and COOP."""
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertIn('Permissions-Policy', response.headers)
        self.assertIn('Cross-Origin-Opener-Policy', response.headers)

    # -------------------------------------------------------------
    # 9. Information Disclosure & Robots Protection
    # -------------------------------------------------------------
    def test_robots_txt_protects_sensitive_endpoints(self):
        """Robots.txt must be accessible and disallow sensitive admin paths."""
        response = self.client.get(reverse('robots_txt'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn("User-agent: *", content)
        self.assertIn("Disallow: /dashboard/", content)
        self.assertIn("Disallow: /admin/", content)
