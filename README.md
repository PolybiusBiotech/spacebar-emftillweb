EMF Till web service
====================

Infrastructure needed to bring up an instance of `quicktill.tillweb`,
plus the public-facing web pages for https://bar.emf.camp/

This is the EMF-specific fork of the project and contains assumptions
about how the EMF till is configured. [There is a separate repo for the generic version of the project here.](https://github.com/sde1000/tillweb)

The infrastructure code in `tillweb/config` has been modified as
little as possible. Most EMF-specific code is in `emf/`


Prerequisites
-------------

You need [poetry](https://python-poetry.org/) and
[npm](https://www.npmjs.com/) installed. You need a
[postgresql](https://www.postgresql.org/) database available.


Configuration
-------------

The package reads `~/.config/emftillweb.toml` at startup. Example
suitable for development:

```
[django]
time_zone = "Europe/London"
mode = "devel"

[till]
database_name = "emfcamp"
currency_symbol = "£"
site_name = "EMF Bars"

[kiosk.tokens.test-token]
locations = ["Kiosk"]
order_prefix = "Kiosk"
source = "kiosk-test"
```

The optional `kiosk.tokens` section configures bearer tokens for the
private kiosk order API.  Each token is scoped to one or more till
stockline locations.  The kiosk order API only creates unpaid saved
orders; kiosk clients should use the public stockline API for the
product list and live stock updates.

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
