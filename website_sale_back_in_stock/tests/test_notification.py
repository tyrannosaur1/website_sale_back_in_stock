# -*- coding: utf-8 -*-
"""The waitlist as a model: uniqueness, the state machine, and the sweep.

The last three tests are the ones the module exists for: a subscription is
mailed once, a row another worker already claimed is not mailed again, and a
website with the feature turned off is not swept at all.
"""
from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged

from .common import StockNotifyHelpers, prepare_shop, product_values


@tagged("post_install", "-at_install")
class TestStockNotification(StockNotifyHelpers, TransactionCase):

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

    # ------------------------------------------------------------------
    # subscription
    # ------------------------------------------------------------------
    def test_a_subscription_waits_for_confirmation(self):
        notification, created = self._subscribe("buyer@example.com")
        self.assertTrue(created)
        self.assertEqual(notification.state, "pending")
        self.assertTrue(notification.token)
        self.assertEqual(notification.lang, self.website.default_lang_id.code or "en_US")
        mails = self._mails(notification)
        self.assertEqual(len(mails), 1)
        self.assertIn(notification.token, mails.body_html)
        self.assertIn("/shop/stock-notify/confirm/", mails.body_html)

    def test_the_same_address_is_one_subscription(self):
        first, created = self._subscribe("Buyer@Example.com")
        second, created_again = self._subscribe("buyer@example.com")
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first, second)
        self.assertEqual(first.token, second.token)
        self.assertEqual(self.Notify.search_count([("email", "=", "buyer@example.com")]), 1)
        # and one confirmation mail, not two
        self.assertEqual(len(self._mails(first)), 1)

    def test_the_database_refuses_a_second_row(self):
        """The uniqueness is the constraint's job, not the search's."""
        self._subscribe("unique@example.com")
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.Notify.create({
                    "product_id": self.product.id,
                    "email": "unique@example.com",
                    "website_id": self.website.id,
                })
        self.assertEqual(self.Notify.search_count([("email", "=", "unique@example.com")]), 1)

    def test_the_same_address_on_another_website_is_another_row(self):
        other_website = self.env["website"].create({"name": "Second Shop"})
        first, _created = self._subscribe("two@example.com")
        second, created_again = self._subscribe("two@example.com", website=other_website)
        self.assertTrue(created_again)
        self.assertNotEqual(first, second)

    def test_without_double_optin_the_address_is_subscribed_at_once(self):
        self.website.stock_notify_double_optin = False
        notification, _created = self._subscribe("fast@example.com")
        self.assertEqual(notification.state, "confirmed")
        self.assertEqual(len(self._mails(notification)), 0)

    def test_an_invalid_address_is_refused(self):
        with self.assertRaises(ValidationError):
            self._subscribe("not-an-email")
        self.assertFalse(self.Notify.search([("email", "=", "not-an-email")]))

    # ------------------------------------------------------------------
    # state machine
    # ------------------------------------------------------------------
    def test_the_confirm_action_subscribes_a_pending_row(self):
        notification, _created = self._subscribe("confirm@example.com")
        notification._action_confirm()
        self.assertEqual(notification.state, "confirmed")
        # confirming twice is not an error and does not change anything
        notification._action_confirm()
        self.assertEqual(notification.state, "confirmed")

    def test_a_cancelled_row_is_not_revived_by_the_old_link(self):
        notification, _created = self._subscribe("gone@example.com")
        notification._action_cancel()
        notification._action_confirm()
        self.assertEqual(notification.state, "cancelled")

    def test_a_notified_row_cannot_be_cancelled_any_more(self):
        notification = self._confirmed("done@example.com")
        self._set_stock(self.product, 1)
        self.Notify._sweep()
        self.assertEqual(notification.state, "sent")
        self.assertEqual(notification._action_cancel(), 0)
        self.assertEqual(notification.state, "sent")

    # ------------------------------------------------------------------
    # the sweep
    # ------------------------------------------------------------------
    def test_the_sweep_sends_one_email_then_never_again(self):
        notification = self._confirmed("once@example.com")
        self._set_stock(self.product, 4)
        self.assertEqual(self.Notify._sweep(), 1)
        self.assertEqual(notification.state, "sent")
        self.assertTrue(notification.sent_at)
        self.assertEqual(len(self._mails(notification)), 1)
        # a second run, which is what two cron workers amount to
        self.assertEqual(self.Notify._sweep(), 0)
        self.assertEqual(len(self._mails(notification)), 1)

    def test_a_row_claimed_by_another_run_is_not_mailed(self):
        """The losing worker found a candidate list that is already stale."""
        notification = self._confirmed("race@example.com")
        self._set_stock(self.product, 2)
        stale = self.Notify.search([("id", "=", notification.id)])
        self.assertEqual(stale.state, "confirmed")
        self.assertTrue(stale._claim(), "the other worker claimed the row")
        self.assertFalse(stale._claim(), "a second claim must lose")
        self.assertEqual(self.Notify._notify_records(stale), 0)
        self.assertEqual(len(self._mails(notification)), 0)
        self.assertEqual(notification.state, "sent")

    def test_nothing_is_sent_while_the_variant_is_out_of_stock(self):
        notification = self._confirmed("wait@example.com")
        self.assertEqual(self.Notify._sweep(), 0)
        self.assertEqual(notification.state, "confirmed")
        self.assertEqual(len(self._mails(notification)), 0)

    def test_an_unsubscribed_row_is_never_mailed(self):
        notification = self._confirmed("unsub@example.com")
        notification._action_cancel()
        self._set_stock(self.product, 6)
        self.assertEqual(self.Notify._sweep(), 0)
        self.assertEqual(len(self._mails(notification)), 0)

    def test_another_variant_does_not_trigger_the_alert(self):
        attribute = self.env["product.attribute"].create({
            "name": "Size",
            "value_ids": [(0, 0, {"name": "Small"}), (0, 0, {"name": "Large"})],
        })
        template = self.env["product.template"].create(product_values(
            self.env,
            name="Two Sizes",
            list_price=12.0,
            attribute_line_ids=[(0, 0, {
                "attribute_id": attribute.id,
                "value_ids": [(6, 0, attribute.value_ids.ids)],
            })],
        ))
        small, large = template.product_variant_ids[0], template.product_variant_ids[1]
        notification = self._confirmed("variant@example.com", product=large)
        self._set_stock(small, 7)
        self.assertEqual(self.Notify._sweep(), 0, "the other variant came back")
        self.assertEqual(notification.state, "confirmed")
        self._set_stock(large, 1)
        self.assertEqual(self.Notify._sweep(), 1)
        self.assertEqual(notification.state, "sent")

    def test_a_website_with_the_waitlist_off_is_not_swept(self):
        """Negative control: the setting really is the gate."""
        notification = self._confirmed("off@example.com")
        self._set_stock(self.product, 3)
        self.website.stock_notify_enabled = False
        self.assertEqual(self.Notify._sweep(), 0)
        self.assertEqual(notification.state, "confirmed")
        self.assertEqual(len(self._mails(notification)), 0)

    # ------------------------------------------------------------------
    # content
    # ------------------------------------------------------------------
    def test_the_email_is_rendered_in_the_subscriber_language(self):
        lang = self.env["res.lang"].with_context(active_test=False).search(
            [("code", "=", "es_ES")], limit=1)
        self.assertTrue(lang, "the Spanish language is missing from this database")
        lang.active = True
        self.template.with_context(lang="es_ES").write({"name": "Lámpara agotada"})
        notification = self._confirmed("es@example.com", lang="es_ES")
        self._set_stock(self.product, 1)
        self.assertEqual(self.Notify._sweep(), 1)
        body = self._mails(notification).body_html
        self.assertIn("Lámpara agotada", body)
        self.assertNotIn("Sold Out Lamp", body)

    def test_the_restock_mail_carries_the_unsubscribe_link(self):
        notification = self._confirmed("links@example.com")
        self._set_stock(self.product, 1)
        self.Notify._sweep()
        body = self._mails(notification).body_html
        self.assertIn("/shop/stock-notify/unsubscribe/%s" % notification.token, body)

    def test_subscribing_again_after_a_notice_rearms_the_row(self):
        notification = self._confirmed("again@example.com")
        old_token = notification.token
        self._set_stock(self.product, 1)
        self.Notify._sweep()
        self.assertEqual(notification.state, "sent")
        self._set_stock(self.product, 0)
        rearmed, created = self._subscribe("again@example.com")
        self.assertTrue(created)
        self.assertEqual(rearmed, notification)
        self.assertNotEqual(rearmed.token, old_token)
        self.assertEqual(rearmed.state, "pending")
        self.assertFalse(rearmed.sent_at)
