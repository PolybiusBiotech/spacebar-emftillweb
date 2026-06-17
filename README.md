EMF Till web service
====================

Infrastructure needed to bring up an instance of `quicktill.tillweb`,
plus the public-facing web pages for https://bar.emf.camp/.

This is the EMF-specific fork of the project and contains assumptions
about how the EMF till is configured. [The generic upstream version is here.](https://github.com/emfcamp/emftillweb)

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

[kiosk.tokens.my-dev-token]   # section key IS the bearer token — use random string in prod
locations = ["Spacebar"]
order_prefix = "SB"
source = "spacebar-kiosk-1"  # human-readable label for audit logs
user = "kiosk"
```

The optional `kiosk.tokens` section configures bearer tokens for the
kiosk order API. Each token is scoped to one or more stockline locations.
`order_prefix` is printed on slips and shown on the OMS board (e.g. `SB 0042`).
`user` is the quicktill user under whose name kiosk transactions are recorded.
`locations` must exactly match the `KIOSK_LOCATION` env var on the kiosk and
the location name assigned to stocklines in the database.


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
| `GET /api/kiosk/orders.json?location=<name>` | None | List live unpaid kiosk orders |
| `POST /api/kiosk/orders.json` | Bearer token | Place a new kiosk order |
| `POST /api/kiosk/orders/expire.json` | Bearer token | Manually expire stale orders |

Orders in `unpaid` state older than 15 minutes are expired automatically
on the next `POST` call. Each order gets a short ref (e.g. `SB 0042`)
from `KioskOrderRef`, a Django-managed counter stored in the SQLite app
database, independent of the quicktill PostgreSQL instance.


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
