# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    stock_notify_enabled = fields.Boolean(
        related="website_id.stock_notify_enabled", readonly=False)
    stock_notify_double_optin = fields.Boolean(
        related="website_id.stock_notify_double_optin", readonly=False)
    stock_notify_template_id = fields.Many2one(
        related="website_id.stock_notify_template_id", readonly=False)
