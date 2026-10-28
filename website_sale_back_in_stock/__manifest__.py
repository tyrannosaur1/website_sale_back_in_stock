# -*- coding: utf-8 -*-
{
    "name": "Back in Stock Notifications",
    "version": "19.0.1.0.0",
    "summary": "Variant-level back-in-stock waitlist with double opt-in, one email per "
               "subscriber and an unsubscribe link",
    "author": "ModForge",
    "license": "OPL-1",
    "category": "Website/Website",
    "depends": ["website_sale_stock", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/mail_templates.xml",
        "data/ir_cron.xml",
        "views/stock_notification_views.xml",
        "views/website_views.xml",
        "views/templates.xml",
    ],
    "installable": True,
    "application": False,
    "assets": {
        "web.assets_frontend": [
            "website_sale_back_in_stock/static/src/css/back_in_stock.css",
        ],
    },
    "price": 39.0,
    "currency": "USD",
}
