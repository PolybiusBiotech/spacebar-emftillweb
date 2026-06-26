EMF Till web service
====================

Infrastructure needed to bring up an instance of `quicktill.tillweb`,
plus the public-facing web pages for https://bar.emf.camp/.

This is the EMF-specific fork of the project and contains assumptions
about how the EMF till is configured. [There is a separate repo for the generic version of the project here.](https://github.com/sde1000/tillweb)

The infrastructure code in `tillweb/config` has been modified as
little as possible. Most EMF-specific code is in `emf/`.


Prerequisites
-------------

You need [poetry](https://python-poetry.org/) and
[npm](https://www.npmjs.com/) installed. You need a
[postgresql](https://www.postgresql.org/) database available.


Configuration
-------------

The package reads `~/.config/emftillweb.toml` at startup. Example
suitable for development:

```toml
[django]
time_zone = "Europe/London"
mode = "devel"

[till]
database_name = "emfcamp"
currency_symbol = "£"
site_name = "EMF Bars"

[kiosk]
token = "<random-token>"             # bearer token the kiosk sends in the Authorization header
user = "kiosk"                       # quicktill user that kiosk transactions are recorded under
barcode_secret = "<random-secret>"   # shared with quicktill-spacebar-plugin; HMAC barcode check digits
cancel_mode = "delete"               # "delete" removes a cancelled order; "void" keeps it on the void report
```

The `[kiosk]` section configures the kiosk order API:

- `token` — the shared bearer token the kiosk sends in the
  `Authorization: Bearer <token>` header. Use a long random string in production.
- `user` — the quicktill user under whose name kiosk transactions are recorded.
- `barcode_secret` — shared with the quicktill-spacebar-plugin, used to generate
  and verify the HMAC check digits on order barcodes.
- `cancel_mode` — how a cancellation is recorded: `"delete"` (default) removes the
  transaction (the audit log is the trail); `"void"` keeps it with voiding lines
  on the till's void report.

The order `location` is supplied per request (it must match the location assigned
to stocklines in the database); it is no longer configured here.


Development
-----------

To set up a development environment, ensure you have `poetry`
installed, and then in the project root run `poetry install` and `npm
install`.

Ensure there is a postgresql database called `emfcamp`. (`createdb
emfcamp` if unsure.) Either restore an existing quicktill database
dump into it (`zcat emfcamp.sql.gz | psql emfcamp` assuming the
`emfcamp` database is empty), or set up a new empty database using
`poetry run runtill -d dbname=emfcamp syncdb`

(If you are a bar team member, you can obtain a database dump from
your profile page.)

The various Django project management commands are then available via
`poetry run tillweb`, for example `poetry run tillweb check`, `poetry
run tillweb migrate`, `poetry run tillweb createsuperuser` and `poetry
run tillweb runserver` to run the development server.

The tillweb repository does not include the packed Javascript and CSS
necessary for the quicktill web interface project to run in
`tillweb/static/bundles/`. To regenerate these run `npm run build`.

The SCSS files in `emf/static/emf/scss/` can be converted to CSS by
running `npm run emfsass`, and you can start a process that watches
the SCSS files for changes by running `npm run emfsass-watch`.

After running `poetry install`, apply migrations:

```sh
poetry run tillweb migrate
```


Kiosk order API
---------------

The kiosk API lets a self-service kiosk place orders against the live till
database without needing direct database access. It is used by
[spacebar-kiosk](https://github.com/PolybiusBiotech/spacebar-kiosk) and
monitored by [spacebar-oms](https://github.com/PolybiusBiotech/spacebar-oms).
Orders recalled at the till are handled by the
[quicktill-spacebar-plugin](https://github.com/PolybiusBiotech/quicktill-spacebar-plugin).

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/stocklines.json?location=<name>` | None | Product list and live stock levels |
| `GET /api/kiosk/orders?location=<name>` | Bearer token | List live kiosk orders (OMS poll) |
| `POST /api/kiosk/orders` | Bearer token | Place a new kiosk order — returns `{ order_ref, barcode }` |
| `GET /api/kiosk/orders/<ref>` | Bearer token | Retrieve a single order |
| `DELETE /api/kiosk/orders/<ref>` | Bearer token + valid HMAC barcode | Cancel an unpaid order. Barcode is supplied in the `Order-Barcode` header and must match `<ref>`. Verifies HMAC before deleting. 403 bad barcode, 404 not found, 409 paid/active. |

Order refs are the **quicktill Transaction ID** — no separate counter. Barcodes use HMAC-SHA1 check digits (`KIOSK:<trans_id><3-digit-decimal-check>`) to prevent forgery; both this server and `quicktill-spacebar-plugin` must share the same `kiosk.barcode_secret`.

Unpaid orders expire 15 minutes after creation. A scheduled `expire_kiosk_orders` management command (run e.g. every minute via cron or a systemd timer) sweeps and deletes them; placing an order no longer triggers expiry as a side effect.


Seeding a fresh database
------------------------

If you are not restoring from a dump, you need to seed the `emfcamp`
database with test data before kiosk orders will work. **Run `syncdb`
first** — it populates `transcodes`, `stockremove`, and other reference
tables:

```sh
poetry run runtill -d dbname=emfcamp syncdb
```

Then seed test stock. Key constraints to be aware of:

- `Department` constructor kwarg is `id=`, **not** `dept=` (the Python attribute is `id`; the DB column is `dept`).
- Continuous `StockLine` rows must have **no** `dept_id`, `capacity`, or `pullthru` — those are only valid for `display`/`regular` linetypes.
- `StockItem` rows for a continuous line must have `stocklineid=None` and `checked=True`. If `stocklineid` is set, `StockType.stockonsale()` returns empty and the kiosk sees no stock.
- An active `Session` row (with `starttime` and `date`) is required before any order can be placed. Without it, the kiosk gets `no-active-session`.

After seeding, set a price for your test stocktype in the tillweb admin
(Stocktypes → set selling price for location `Spacebar`).
