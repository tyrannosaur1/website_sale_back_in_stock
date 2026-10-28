# -*- coding: utf-8 -*-
"""Public routes of the waitlist: subscribe, confirm, unsubscribe.

Every route is a plain form post or a link click -- no JavaScript is needed
to use the feature, and none is needed to test it.
"""
import logging

from odoo import http
from odoo.http import request
from werkzeug.exceptions import NotFound

from ..models.website import _current_website

_logger = logging.getLogger(__name__)

#: Query parameter the product page reads back to show the outcome.
STATUS_PARAM = "stock_notify"


class StockNotificationController(http.Controller):

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _website(self):
        return _current_website(request.env)

    def _find(self, token):
        """The row behind a link, or 404. Never leaks whether a token exists."""
        notification = request.env["website.stock.notification"].sudo().search(
            [("token", "=", token or "")], limit=1)
        if not notification:
            raise NotFound()
        return notification

    def _product(self, product_id):
        try:
            product = request.env["product.product"].sudo().browse(int(product_id))
        except (TypeError, ValueError):
            return request.env["product.product"].sudo().browse()
        return product if product.exists() else product.browse()

    def _back_to_product(self, product, status):
        url = product.website_url or "/shop"
        separator = "&" if "?" in url else "?"
        return "%s%s%s=%s" % (url, separator, STATUS_PARAM, status)

    def _render(self, notification, kind):
        return request.render("website_sale_back_in_stock.notification_page", {
            "notification": notification,
            "kind": kind,
            "website": self._website(),
        })

    # ------------------------------------------------------------------
    # routes
    # ------------------------------------------------------------------
    @http.route("/shop/stock-notify/subscribe", type="http", auth="public",
                methods=["POST"], website=True, csrf=True, sitemap=False)
    def subscribe(self, product_id=None, email=None, **kw):
        """Record the visitor's interest in one variant.

        The answer is always a redirect back to the product page: a POST that
        renders a page of its own is re-posted when the visitor reloads, and
        the form is one click away from a page they will reload.
        """
        website = self._website()
        product = self._product(product_id)
        if (not product or not product._stock_notify_is_stock_managed()
                or not getattr(product, "is_published", True)
                or not website.stock_notify_enabled):
            raise NotFound()
        Notification = request.env["website.stock.notification"].sudo()
        normalized = Notification._normalize_email(email)
        if not normalized:
            return request.redirect(self._back_to_product(product, "invalid"), code=303)
        partner = request.env.user.partner_id
        notification, _created = Notification._subscribe(
            product, normalized, website,
            partner=None if request.env.user._is_public() else partner,
            lang=website._stock_notify_language())
        status = "confirmed" if notification.state == "confirmed" else "pending"
        return request.redirect(self._back_to_product(product, status), code=303)

    @http.route("/shop/stock-notify/confirm/<string:token>", type="http",
                auth="public", methods=["GET"], website=True, sitemap=False)
    def confirm(self, token, **kw):
        """The double opt-in click.

        A cancelled subscription is not revived by a link that was sent
        before the visitor unsubscribed: the row is found and reported, its
        state is left alone.
        """
        notification = self._find(token)
        notification._action_confirm()
        return self._render(notification, notification.state)

    @http.route("/shop/stock-notify/unsubscribe/<string:token>", type="http",
                auth="public", methods=["GET", "POST"], website=True,
                csrf=True, sitemap=False)
    def unsubscribe(self, token, **kw):
        """Ask first, cancel on the post.

        A GET only shows the button. Mail scanners, preview fetchers and
        antivirus proxies all follow links in a message, and a page that
        unsubscribes on GET lets any of them drop a customer from the
        waitlist before the customer has read the email.
        """
        notification = self._find(token)
        if request.httprequest.method == "POST":
            notification._action_cancel()
            return self._render(notification, "unsubscribed")
        return self._render(notification, "unsubscribe_confirm")
