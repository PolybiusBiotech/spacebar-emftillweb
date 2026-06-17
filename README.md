EMF Till web service
====================

Infrastructure needed to bring up an instance of `quicktill.tillweb`,
plus the public-facing web pages for https://bar.emf.camp/ and the
**kiosk order API** used by self-service drink kiosks at EMF bars.

This is the EMF-specific fork of the project and contains assumptions
about how the EMF till is configured. [There is a separate repo for the generic version of the project here.](https://github.com/sde1000/tillweb)

The infrastructure code in `tillweb/config` has been modified as
little as possible. Most EMF-specific code is in `emf/`.


Kiosk order API
---------------

The kiosk API lets a self-service kiosk place drink orders against the
live till database without needing direct database access.

### Public endpoints (no auth required)

| Endpoint | Purpose |
|---|---|
| `GET /api/stocklines.json?location=<name>` | Product list and live stock levels |
| `GET /api/kiosk/orders.json?location=<name>` | List live unpaid kiosk orders (used by the OMS) |

### Authenticated endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/kiosk/orders.json` | Bearer token | Place a new kiosk order |
| `POST /api/kiosk/orders/expire.json` | Bearer token | Manually expire stale orders |

Authentication uses a static bearer token sent in the `Authorization: Bearer <token>` header. Tokens are configured in `emftillweb.toml` under `[kiosk.tokens.<name>]` and are scoped to a location. The token name becomes the `source` field in order metadata.

### Order flow

1. Kiosk fetches product list via `GET /api/stocklines.json?location=Spacebar`
2. Customer builds a basket; kiosk calls `POST /api/kiosk/orders.json` with `Authorization: Bearer <token>` → receives an `order_ref` (e.g. `SB 0042`)
3. Customer pays at the till; barstaff recall the order by scanning the printed QR code (handled by the [quicktill-kiosk-plugin](../quicktill-kiosk-plugin/))
4. The OMS ([spacebar-oms](../spacebar-oms/)) polls `GET /api/kiosk/orders.json` and shows orders on a customer-facing display

### Order states

| State | Meaning |
|---|---|
| `unpaid` | Order placed, awaiting till payment |
| `processing` | Payment taken, barstaff preparing drinks |
| `collect` | Ready for collection (set by barstaff via OMS) |

Orders in `unpaid` state older than 15 minutes are expired automatically on the next `POST /api/kiosk/orders.json` call.

### Order ref counter

Each order gets a short reference (`SB 0042`) generated from `KioskOrderRef`, a Django-managed auto-increment counter stored in the SQLite app database (`tillweb_db.sqlite3`). This is independent of the quicktill PostgreSQL database and survives deployments where multiple tills share the same postgres instance.


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

[kiosk.tokens.spacebar-kiosk-1]
locations = ["Spacebar"]
order_prefix = "SB"
source = "spacebar-kiosk-1"
user = "kiosk"
```

The `locations` value must exactly match the `KIOSK_LOCATION` env var on
the kiosk, and must match the location name assigned to stocklines in the
database. `order_prefix` is the label written into order metadata and shown
on the OMS board (e.g. `SB 0042`). `user` is the quicktill user under whose
name kiosk transactions are recorded.

For production with two kiosks, add a second token block:

```toml
[kiosk.tokens.spacebar-kiosk-2]
locations = ["Spacebar"]
order_prefix = "SB"
source = "spacebar-kiosk-2"
user = "kiosk"
```

Each kiosk uses a different token so faults can be traced by `source` in
logs.


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

After running `poetry install`, apply migrations (including the
`KioskOrderRef` table):

```sh
poetry run tillweb migrate
```


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
(Stocktypes → set selling price for location `Spacebar`), then confirm
migrations are applied:

```sh
poetry run tillweb migrate
```
