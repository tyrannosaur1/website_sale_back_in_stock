# -*- coding: utf-8 -*-
"""The public routes, through a real request.

Nothing here calls the controller method directly: the form is posted the way
a browser posts it, the token is the one the page rendered, and the links are
followed the way a mail client follows them.
"""
import re

from odoo.tests.common import HttpCase, tagged

from .common import StockNotifyHelpers, prepare_shop


@tagged("post_install", "-at_install")
class TestStockNotificationHttp(StockNotifyHelpers, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website, cls.template = prepare_shop(cls.env)
        cls.product = cls.template.product_variant_id
        cls.stock_location = cls.env.ref("stock.stock_location_stock")
        cls.Notify = cls.env["website.stock.notification"]

    def setUp(self):
        super().setUp()
        self._keep_outgoing_mails()
        self.Notify.search([]).unlink()
        self._set_stock(self.product, 0)
        self.website.write({
            "stock_notify_enabled": True,
            "stock_notify_double_optin": True,
        })

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _page(self):
        # Each test asks for its own query string: 19 and 20 cache a rendered
        # shop page per path, and another test's copy would otherwise answer.
        response = self.url_open("%s?wsb=%s" % (
            self.template.website_url, self._testMethodName))
        self.assertEqual(response.status_code, 200)
        return response.text

    def _csrf(self, html):
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
        self.assertTrue(match, "the page carries no csrf token")
        return match.group(1)

    def _post_subscribe(self, email, follow=False):
        return self.url_open("/shop/stock-notify/subscribe", data={
            "csrf_token": self._csrf(self._page()),
            "product_id": str(self.product.id),
            "email": email,
        }, allow_redirects=follow)

    # ------------------------------------------------------------------
    # the product page
    # ------------------------------------------------------------------
    def test_the_form_is_offered_on_a_sold_out_product(self):
        html = self._page()
        self.assertIn('id="wsb_notify_email"', html)
        self.assertIn('action="/shop/stock-notify/subscribe"', html)

    def test_the_form_is_not_offered_when_the_product_is_in_stock(self):
        """Negative control: the shelf is what decides, not the module."""
        self._set_stock(self.product, 5)
        html = self._page()
        self.assertNotIn('id="wsb_notify_email"', html)

    def test_the_form_is_not_offered_when_the_waitlist_is_off(self):
        """Negative control: turning the feature off hides it everywhere."""
        # The token is taken while the feature is on: the disabled page has no
        # form of ours left to read one from, and a 400 would hide the 404
        # that says the route itself is closed.
        token = self._csrf(self._page())
        self.website.stock_notify_enabled = False
        html = self._page()
        self.assertNotIn('id="wsb_notify_email"', html)
        response = self.url_open("/shop/stock-notify/subscribe", data={
            "csrf_token": token,
            "product_id": str(self.product.id),
            "email": "closed@example.com",
        }, allow_redirects=False)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.Notify.search([("email", "=", "closed@example.com")]))

    # ------------------------------------------------------------------
    # subscribe
    # ------------------------------------------------------------------
    def test_the_form_post_creates_a_pending_subscription(self):
        response = self._post_subscribe("http@example.com")
        self.assertEqual(response.status_code, 303)
        self.assertIn("stock_notify=pending", response.headers["Location"])
        notification = self.Notify.search([("email", "=", "http@example.com")])
        self.assertEqual(len(notification), 1)
        self.assertEqual(notification.state, "pending")
        self.assertEqual(notification.product_id, self.product)

    def test_an_invalid_address_is_sent_back_without_a_row(self):
        response = self._post_subscribe("not-an-email")
        self.assertEqual(response.status_code, 303)
        self.assertIn("stock_notify=invalid", response.headers["Location"])
        self.assertFalse(self.Notify.search([]))

    def test_the_post_is_refused_without_a_csrf_token(self):
        response = self.url_open("/shop/stock-notify/subscribe", data={
            "product_id": str(self.product.id),
            "email": "nocsrf@example.com",
        }, allow_redirects=False)
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(self.Notify.search([("email", "=", "nocsrf@example.com")]))

    # ------------------------------------------------------------------
    # links
    # ------------------------------------------------------------------
    def test_unknown_tokens_answer_404(self):
        for path in ("/shop/stock-notify/confirm/deadbeef",
                     "/shop/stock-notify/unsubscribe/deadbeef"):
            response = self.url_open(path)
            self.assertEqual(response.status_code, 404, path)

    def test_the_confirmation_link_subscribes(self):
        notification, _created = self._subscribe("confirm@example.com")
        self.assertEqual(notification.state, "pending")
        response = self.url_open("/shop/stock-notify/confirm/%s" % notification.token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(notification.state, "confirmed")
        self.assertIn("on the list", response.text)

    def test_the_unsubscribe_page_asks_before_cancelling(self):
        """A GET must not cancel: mail scanners follow every link in a message."""
        notification = self._confirmed("careful@example.com")
        response = self.url_open("/shop/stock-notify/unsubscribe/%s" % notification.token)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Yes, unsubscribe", response.text)
        self.assertEqual(notification.state, "confirmed")

    def test_the_unsubscribe_post_cancels(self):
        notification = self._confirmed("leaving@example.com")
        page = self.url_open("/shop/stock-notify/unsubscribe/%s" % notification.token)
        response = self.url_open("/shop/stock-notify/unsubscribe/%s" % notification.token, data={
            "csrf_token": self._csrf(page.text),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(notification.state, "cancelled")
        self.assertIn("unsubscribed", response.text)
