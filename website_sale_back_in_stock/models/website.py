# -*- coding: utf-8 -*-
from odoo import fields, models


def _current_website(env):
    """The website serving the request, on every series.

    20 dropped ``website.get_current_website``; there the request itself
    carries the website. Outside a request -- a cron, a shell, a model test --
    the first website is the shop.
    """
    from odoo.http import request

    try:
        website = request.website
    except RuntimeError:
        # no request bound at all (cron, shell)
        website = None
    except AttributeError:
        website = None
    if website:
        return website
    try:
        website = request.env.website
    except (RuntimeError, AttributeError):
        website = None
    if website:
        return website
    Website = env["website"]
    getter = getattr(Website, "get_current_website", None)
    return getter() if getter else Website.search([], limit=1)


class Website(models.Model):
    _inherit = "website"

    stock_notify_enabled = fields.Boolean(
        string="Back-in-Stock Waitlist", default=True,
        help="Let visitors leave their email on an out-of-stock product and be "
             "told when it is available again. Turning this off hides the form, "
             "refuses subscriptions and stops the restock sweep for this website; "
             "existing rows are kept.",
    )
    stock_notify_double_optin = fields.Boolean(
        string="Confirm Subscriptions by Email", default=True,
        help="Send a confirmation link before a subscriber is eligible for the "
             "restock email. Keeps typos and third-party addresses off the list. "
             "Turn it off to subscribe from the single form submit.",
    )
    stock_notify_template_id = fields.Many2one(
        "mail.template", string="Restock Email",
        domain="[('model', '=', 'website.stock.notification')]",
        default=lambda self: self.env.ref(
            "website_sale_back_in_stock.mail_template_back_in_stock",
            raise_if_not_found=False),
        help="Email sent when a subscribed product is back in stock. Rendered in "
             "the subscriber's language. The confirmation email is the module "
             "template 'Back in Stock: Confirm Subscription'.",
    )

    def _stock_notify_language(self):
        """Language code the current shop front is served in."""
        from odoo.http import request

        self.ensure_one()
        try:
            code = (request.env.context.get("lang") or "").strip()
        except RuntimeError:
            # no request bound: a cron, a shell or a model test
            code = ""
        if not code:
            code = (self.default_lang_id.code or "en_US") if self.default_lang_id else "en_US"
        return code
