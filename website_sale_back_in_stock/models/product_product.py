# -*- coding: utf-8 -*-
from odoo import models

from .website import _current_website


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _stock_notify_is_stock_managed(self):
        """Whether the variant is a good that is kept in stock.

        16-18 answer with ``type == 'product'``; 19 renamed the goods type and
        made ``is_storable`` the flag, so both spellings are read here.
        """
        self.ensure_one()
        if self.type == "product":
            return True
        return bool(getattr(self, "is_storable", False))

    def _stock_notify_form_available(self):
        """Whether the product page should offer the waitlist form.

        The same three conditions the shop uses to refuse the sale: the
        variant is stock-managed, the shop is not letting visitors order it
        anyway, and there is nothing left to ship. Showing the form on a
        product that can be bought would collect emails nobody needs.

        The record is read as sudo: ``qty_available`` walks the warehouses
        when it is computed, and the visitor looking at the page has no
        Inventory access at all -- reading it as them answers the product page
        with a 403 instead of a stock level.
        """
        self.ensure_one()
        product = self.sudo()
        website = _current_website(self.env)
        if not website.stock_notify_enabled:
            return False
        if not product._stock_notify_is_stock_managed():
            return False
        if not getattr(product, "is_published", True):
            return False
        # 20 has no per-product flag any more, hence the lookup instead of a
        # plain attribute: a series without the flag falls through to the
        # stock level, which is the condition that matters.
        if getattr(product, "allow_out_of_stock_order", False):
            return False
        return product.qty_available <= 0
