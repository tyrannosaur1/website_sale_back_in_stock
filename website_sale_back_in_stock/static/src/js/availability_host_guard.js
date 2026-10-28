/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { WebsiteSale } from "@website_sale/interactions/website_sale";

/**
 * The waitlist replaces the shop's availability host on sold-out products
 * (``div.availability_messages``), so the shop's "notify me" block is never
 * built and there is no button left to click.
 *
 * 19's stock renderer appends its template to that host without checking it
 * is still there, so a sold-out page would throw on every variant change.
 * Skipping the update is the whole point: there is no host to render into.
 */
patch(WebsiteSale.prototype, {
    async _onChangeCombinationStock(ev, parent, combination) {
        if (!parent.querySelector("div.availability_messages")) {
            return;
        }
        return super._onChangeCombinationStock(ev, parent, combination);
    },
});
