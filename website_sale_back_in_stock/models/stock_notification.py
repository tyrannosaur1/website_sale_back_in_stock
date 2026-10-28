# -*- coding: utf-8 -*-
"""Back-in-stock waitlist.

A visitor who lands on an out-of-stock product leaves an email address; the
row created here is what decides, later, whether a restock email is due. The
row is kept per variant, per website and per address, carries the language
the visitor was browsing in and a token used by the confirm and unsubscribe
links, so both links keep working after the browser session is gone.

Two properties matter more than the rest, and both are pinned by tests:

* one subscription per (variant, address, website). A visitor who submits the
  form twice, reloads the page or clicks "notify me" again must not end up
  with two rows -- and therefore two emails. The uniqueness is a database
  constraint, not a check the controller performs, so two requests racing
  each other cannot both insert.

* one email per subscription. The sweep claims a row with a conditional
  ``UPDATE ... WHERE state = 'confirmed'`` and only the transaction whose
  update matched a row may send. Two cron workers, or a worker and a manual
  send, are serialised by the row lock: the loser's update matches nothing
  and it sends nothing. Marking the row after sending would be a check-then-
  act and would mail the same subscriber twice.
"""
import logging
import secrets

from odoo import _, api, fields, models, tools
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

#: Rows one sweep looks at. Subscriptions beyond it are picked up by the next
#: run; the sweep is a background job, not a report, so it must not hold a
#: transaction open for as long as the waitlist is long.
SWEEP_BATCH_SIZE = 500

#: States a row can be cancelled from. ``sent`` is terminal: the email has
#: already left, there is nothing left to stop.
CANCELLABLE_STATES = ("pending", "confirmed")


class WebsiteStockNotification(models.Model):
    _name = "website.stock.notification"
    _description = "Back-in-Stock Notification"
    _order = "create_date desc, id desc"
    _rec_name = "email"

    product_id = fields.Many2one(
        "product.product", string="Product Variant", required=True,
        ondelete="cascade", index=True,
        help="The exact variant the visitor asked about. Restocking another "
             "variant of the same template does not notify this subscriber.",
    )
    product_tmpl_id = fields.Many2one(
        "product.template", related="product_id.product_tmpl_id",
        store=True, index=True, string="Product",
    )
    email = fields.Char(required=True, index=True)
    partner_id = fields.Many2one("res.partner", ondelete="set null", index=True)
    website_id = fields.Many2one(
        "website", required=True, ondelete="cascade", index=True)
    lang = fields.Char(
        help="Language the visitor was browsing in. Both emails are rendered "
             "in it, whatever the language of the back office.",
    )
    state = fields.Selection(
        [("pending", "Awaiting confirmation"),
         ("confirmed", "Subscribed"),
         ("sent", "Notified"),
         ("cancelled", "Cancelled")],
        required=True, default="pending", index=True, copy=False,
    )
    token = fields.Char(
        required=True, index=True, copy=False,
        help="Opaque key used by the confirmation and the unsubscribe links.",
    )
    sent_at = fields.Datetime(readonly=True, copy=False)

    # 16-18 spell the constraint in `_sql_constraints`; 19 dropped that
    # attribute, so the port declares it here instead.
    _sql_constraints = [
        ("product_email_website_uniq",
         "unique(product_id, email, website_id)",
         "This email address is already subscribed to this product on this website."),
        ("token_uniq", "unique(token)", "The notification token must be unique."),
    ]

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    @api.model
    def _normalize_email(self, email):
        """The address as it is stored: normalized, lower case.

        Storing the raw string would let ``Alice@Example.com`` and
        ``alice@example.com`` pass the unique constraint as two rows and
        deliver two emails to one inbox.
        """
        return (tools.email_normalize((email or "").strip()) or "").lower()

    @api.model
    def _new_token(self):
        return secrets.token_hex(24)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("email"):
                vals["email"] = self._normalize_email(vals["email"])
            if not vals.get("token"):
                vals["token"] = self._new_token()
        return super().create(vals_list)

    @api.model
    def _subscribe(self, product, email, website, partner=None, lang=None):
        """Subscribe *email* to *product* on *website*; return (row, created).

        Idempotent: an address that is already waiting is handed back as it
        is, token unchanged, without a second confirmation mail. A row that
        had been cancelled, or already notified, is re-armed with a fresh
        token -- the old links must stop working once the subscription is
        used up.
        """
        email = self._normalize_email(email)
        if not email:
            raise ValidationError(_("Please enter a valid email address."))
        double_optin = bool(website.stock_notify_double_optin)
        state = "pending" if double_optin else "confirmed"
        domain = [
            ("product_id", "=", product.id),
            ("email", "=", email),
            ("website_id", "=", website.id),
        ]
        notification = self.sudo().search(domain, limit=1)
        if notification:
            if notification.state in ("pending", "confirmed"):
                return notification, False
            notification.write({
                "state": state,
                "sent_at": False,
                "token": self._new_token(),
                "lang": lang or notification.lang,
                "partner_id": partner.id if partner else notification.partner_id,
            })
        else:
            values = {
                "product_id": product.id,
                "email": email,
                "website_id": website.id,
                "lang": lang or (website.default_lang_id.code or "en_US"),
                "state": state,
                "partner_id": partner.id if partner else False,
            }
            try:
                # A second request may insert the same triple between the
                # search above and this insert. The savepoint keeps the
                # IntegrityError from poisoning the request's transaction;
                # the row the other request committed is then the answer.
                with self.env.cr.savepoint():
                    notification = self.sudo().create(values)
            except Exception:
                notification = self.sudo().search(domain, limit=1)
                if not notification:
                    raise
                return notification, False
        if double_optin:
            notification._send_confirmation_mail()
        return notification, True

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    def _action_confirm(self):
        """Turn a pending subscription into a confirmed one."""
        for notification in self:
            if notification.state == "pending":
                notification.state = "confirmed"
        return True

    def _action_cancel(self):
        """Stop the subscription. A notified row is left as it is."""
        cancellable = self.filtered(lambda n: n.state in CANCELLABLE_STATES)
        cancellable.write({"state": "cancelled"})
        return len(cancellable)

    def _claim(self):
        """Claim the right to send the restock email; True for one caller only.

        The state test and the write are one statement, so they cannot be
        interleaved: a second transaction blocks on the row lock, re-reads
        the row after the first commits, finds ``sent`` and matches nothing.
        ``rowcount`` is therefore the only answer to "may I send", and no
        Python-side check can replace it.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        # RETURNING rather than rowcount: the row that came back is the claim,
        # and nothing else in the environment can shadow the answer.
        self.env.cr.execute(
            "UPDATE website_stock_notification "
            "SET state = 'sent', sent_at = %s, write_date = %s "
            "WHERE id = %s AND state = 'confirmed' RETURNING id",
            (now, now, self.id))
        claimed = bool(self.env.cr.fetchone())
        self.invalidate_recordset(["state", "sent_at", "write_date"])
        return claimed

    # ------------------------------------------------------------------
    # Restock sweep
    # ------------------------------------------------------------------
    @api.model
    def _product_is_in_stock(self, product, quantities):
        """Whether *product* may be announced. ``quantities`` is prefetched."""
        if not product or not product.active:
            return False
        # A service or a consumable never has an on-hand figure to come back
        # from: its subscribers would wait forever, so it is never announced.
        if not product._stock_notify_is_stock_managed():
            return False
        return quantities.get(product.id, 0.0) > 0

    @api.model
    def _pending_candidates(self):
        return self.sudo().search([
            ("state", "=", "confirmed"),
            ("website_id.stock_notify_enabled", "=", True),
        ], order="id", limit=SWEEP_BATCH_SIZE)

    @api.model
    def _notify_records(self, notifications):
        """Send the restock email to each record this run is allowed to send.

        The stock level is read once for the whole batch: ``qty_available``
        is not stored, so reading it per row would run one query per
        subscriber on a waitlist that is exactly as long as a busy shop's.
        """
        if not notifications:
            return 0
        products = notifications.product_id.sudo()
        # `read` computes the field for the whole recordset at once; reading
        # it attribute by attribute would run one quantities query per row.
        quantities = {
            row["id"]: row["qty_available"]
            for row in products.read(["qty_available"])
        }
        sent = 0
        for notification in notifications:
            if not self._product_is_in_stock(notification.product_id, quantities):
                continue
            # This is the whole "once" guarantee: whoever loses the claim
            # sends nothing, whether it lost to another worker or to an
            # earlier run of this one.
            if not notification._claim():
                continue
            try:
                # The claim and the mail are one unit: a template that cannot
                # be rendered must leave the row subscribed, not consumed,
                # or the subscriber is silently dropped.
                with self.env.cr.savepoint():
                    notification._send_restock_mail()
            except Exception:
                _logger.exception(
                    "Back-in-stock email for notification %s failed; the row "
                    "stays subscribed and the next run retries it.", notification.id)
                notification.invalidate_recordset(["state", "sent_at"])
                continue
            sent += 1
        return sent

    @api.model
    def _sweep(self):
        """Announce every confirmed subscription whose variant is back."""
        candidates = self._pending_candidates()
        if len(candidates) == SWEEP_BATCH_SIZE:
            _logger.info(
                "Back-in-stock sweep reached its batch size; the rest of the "
                "waitlist is handled by the next run.")
        return self._notify_records(candidates)

    @api.model
    def _cron_sweep(self):
        sent = self._sweep()
        if sent:
            _logger.info("Sent %s back-in-stock emails.", sent)
        return sent

    # ------------------------------------------------------------------
    # Mail
    # ------------------------------------------------------------------
    def _mail_template(self, xmlid):
        return self.env.ref(xmlid, raise_if_not_found=False) or self.env["mail.template"]

    def _render_lang(self):
        self.ensure_one()
        return self.lang or (self.website_id.default_lang_id.code or "en_US")

    def _send_mail(self, template):
        self.ensure_one()
        if not template:
            raise ValidationError(_("The email template is missing."))
        # `send_mail` renders with the environment's language, so a visitor
        # who subscribed in Spanish gets the Spanish subject and body even
        # when the cron runs as the administrator.
        template.sudo().with_context(lang=self._render_lang()).send_mail(
            self.id, force_send=True)

    def _send_confirmation_mail(self):
        self._send_mail(self._mail_template(
            "website_sale_back_in_stock.mail_template_confirm"))

    def _send_restock_mail(self):
        template = self.website_id.stock_notify_template_id or self._mail_template(
            "website_sale_back_in_stock.mail_template_back_in_stock")
        self._send_mail(template)

    # ------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------
    @api.model
    def _base_url(self):
        website = self.website_id
        domain = (website.domain or "").strip() if website else ""
        if domain:
            return domain if domain.startswith(("http://", "https://")) else "https://" + domain
        # No domain on the website (the usual case while it is being built):
        # web.base.url is what the back office itself uses.
        rows = self.env["ir.config_parameter"].sudo().search_read(
            [("key", "=", "web.base.url")], ["value"], limit=1)
        return ((rows and rows[0]["value"]) or "").rstrip("/")

    def _stock_notify_product_url(self):
        self.ensure_one()
        path = self.product_tmpl_id.website_url or "/shop"
        return self._base_url() + path

    def _stock_notify_confirm_url(self):
        self.ensure_one()
        return "%s/shop/stock-notify/confirm/%s" % (self._base_url(), self.token)

    def _stock_notify_unsubscribe_url(self):
        self.ensure_one()
        return "%s/shop/stock-notify/unsubscribe/%s" % (self._base_url(), self.token)
