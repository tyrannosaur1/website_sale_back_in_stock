# Back in Stock Notifications

Let a shopper ask to be told when an out-of-stock variant is back, confirm the request, and get exactly one email when it is replenished.

Odoo 16.0 – 20.0. One branch per series, the branch name is the series.

## What it does

- Takes over the shop's stock notification widget so there is one form, not two, and it works per variant.
- Double opt-in: the address is only counted once the shopper clicks the link in the confirmation mail, so a typo or a prank does not mail a stranger.
- When stock comes back, each waiting shopper gets one notification; the entry is marked as sent and is not mailed twice.
- The waiting list is a backend list with the product, the variant, the address, the state and when the mail went out.
- Unsubscribing works from the mail, and a shopper can be removed by hand.

## Install

1. Copy the module into your addons path.
2. Update the app list, install **Back in Stock Notifications**.
3. Inventory › Configuration › Settings: choose the double opt-in and the notification template.

## Translations

Chinese, Spanish, German, French, Italian, Dutch, Polish, Brazilian Portuguese and Russian ship with the module.

## License

OPL-1. See `LICENSE`.

## Support

Open an issue on this repository.
