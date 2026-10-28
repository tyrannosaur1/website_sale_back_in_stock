# -*- coding: utf-8 -*-
"""Shared fixtures for the waitlist suites.

The shop is built the way a merchant builds it: a published, stock-managed
product that must not be sold once the shelf is empty. Nothing here writes to
the database from a page render, so the suites also run against 18/20 where
the product page is served on a read-only cursor.
"""

from unittest.mock import patch


def product_values(env, **extra):
    """Product values that describe a stock-managed good on every series.

    16-18 spell it ``type='product'``; 19 renamed the goods type and made
    ``is_storable`` the flag. The optional website flags are set when the
    series still has them.
    """
    values = {"taxes_id": [(5, 0, 0)]}
    fields = env["product.template"]._fields
    if "is_storable" in fields:
        values["type"] = "consu"
        values["is_storable"] = True
    else:
        values["type"] = "product"
    if "is_published" in fields:
        values["is_published"] = True
    # 20 dropped the per-product flag; the module reads it with getattr.
    if "allow_out_of_stock_order" in fields:
        values["allow_out_of_stock_order"] = False
    values.update(extra)
    return values


def prepare_shop(env):
    """A website with the waitlist on and one sold-out product."""
    website = env["website"].search([], limit=1)
    if not website:
        website = env["website"].create({"name": "Waitlist Shop"})
    website.write({
        "stock_notify_enabled": True,
        "stock_notify_double_optin": True,
    })
    template = env["product.template"].create(product_values(
        env, name="Sold Out Lamp", list_price=42.0))
    return website, template


class StockNotifyHelpers(object):
    """Helpers shared by the model suite and the HTTP suite."""

    def _keep_outgoing_mails(self):
        """Stop only the transport, so the rows stay countable.

        In test mode Odoo reports a delivery as successful without an SMTP
        server and then deletes the ``mail.mail`` row (``auto_delete``), so a
        suite that counts rows afterwards counts nothing. The templates still
        render and the rows are still created -- what is stubbed is the step
        that would hand them to a server.
        """
        MailMail = type(self.env["mail.mail"])
        patcher = patch.object(MailMail, "send")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _set_stock(self, product, quantity):
        """Put *quantity* units of *product* on the shelf of the main warehouse."""
        Quant = self.env["stock.quant"].with_context(inventory_mode=True)
        quant = Quant.search([
            ("product_id", "=", product.id),
            ("location_id", "=", self.stock_location.id),
        ], limit=1)
        if quant:
            quant.write({"inventory_quantity": quantity})
        else:
            quant = Quant.create({
                "product_id": product.id,
                "location_id": self.stock_location.id,
                "inventory_quantity": quantity,
            })
        quant.action_apply_inventory()
        product.invalidate_recordset(["qty_available"])

    def _subscribe(self, email, product=None, website=None, lang=None, partner=None):
        return self.Notify._subscribe(
            product or self.product,
            email,
            website or self.website,
            partner=partner,
            lang=lang,
        )

    def _confirmed(self, email, product=None, lang=None):
        """A subscription past the double opt-in, with no mail left behind."""
        notification, _created = self._subscribe(email, product=product, lang=lang)
        notification._action_confirm()
        self.assertEqual(notification.state, "confirmed")
        # The confirmation mail is not what these tests are about; drop it so
        # the counts below speak about the restock email alone.
        self._mails(notification).unlink()
        return notification

    def _mails(self, notification):
        return self.env["mail.mail"].search([
            ("email_to", "ilike", notification.email),
        ])
