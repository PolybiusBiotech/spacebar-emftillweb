# This module implements a command line program that connects to the
# till database and to redis, listens for notifications from the till
# database and pushes updated objects to redis. It is expected that a
# single instance of this program will be run as a daemon.

from django.core.management.base import BaseCommand
import redis
import sdnotify
from emf import tilldb  # noqa: F401
import sqlalchemy.event
from sqlalchemy.orm import joinedload, undefer
from sqlalchemy.sql import func
from django.core.serializers.json import DjangoJSONEncoder
from decimal import Decimal
from quicktill import event, listen, td
from quicktill.models import StockLine, StockType, StockItem, \
    Unit, StockOut, Transline
from emf.tilldb import tillsession
from emf.api_objects import \
    stockline_to_dict, stocktype_to_dict, stockitem_to_dict

rcon = None
json = DjangoJSONEncoder(indent=2)
mainloop = None

qcounter_enabled = False
show_queries = False


class qcounter:
    def __init__(self, task=None):
        self.queries = []
        self.session = td.s
        self.task = task

    def __enter__(self):
        if qcounter_enabled:
            sqlalchemy.event.listen(
                self.session.get_bind(), "before_cursor_execute",
                self._querylog_callback)

    def __exit__(self, type, value, traceback):
        if qcounter_enabled:
            sqlalchemy.event.remove(
                self.session.get_bind(), "before_cursor_execute",
                self._querylog_callback)
            print(f"{self.task or ''} -> {len(self.queries)} queries used")
            if show_queries:
                for n, q in enumerate(self.queries, start=1):
                    print(f"{n}: {q}")

    def _querylog_callback(self, _conn, _cur, query, params, *_):
        self.queries.append(query)


def publish(d):
    text = json.encode(d)
    rcon.set(d['key'], text)


def delete(key):
    rcon.delete(key)


def publish_totals_by_unit():
    # We must be called with an ORM session in progress
    units = td.s.query(Unit, func.sum(StockOut.qty))\
                .join(StockType)\
                .join(StockItem)\
                .join(StockOut)\
                .filter(StockOut.removecode_id == 'sold')\
                .group_by(Unit)\
                .all()

    publish({
        'type': "totals by unit",
        'key': 'totals/by-unit',
        'units': {
            unit.description: (qty / unit.base_units_per_sale_unit).quantize(
                Decimal("0.1")) for unit, qty in units},
    })


def publish_cup_reuse_count():
    # Must be called with an ORM session in progress
    re_used = td.s.query(func.sum(Transline.items))\
        .filter(Transline.dept_id == 100)\
        .scalar() or 0

    publish({
        'type': "cups re-used",
        'key': 'totals/cups-re-used',
        'count': -re_used,
    })


def notify_stockline_change(id_str):
    try:
        id = int(id_str)
    except Exception:
        return
    with td.orm_session():
        with qcounter("stockline_change"):
            sos = joinedload(StockLine.stockonsale)
            st = sos.joinedload(StockItem.stocktype)
            sl = td.s.query(StockLine)\
                     .options(sos,
                              st,
                              sos.undefer(StockItem.remaining),
                              st.undefer(StockType.total_remaining),
                              st.undefer(StockType.total))\
                     .get(id)
            if not sl:
                delete(f"stockline/{id}")
                return
            publish(stockline_to_dict(sl))
            # If the stockline is continuous and has a stocktype,
            # publish the stocktype as well because it may just have
            # been put on sale on the stockline
            if sl.linetype == "continuous" and sl.stocktype:
                publish(stocktype_to_dict(sl.stocktype))


def notify_stocktype_change(id_str):
    try:
        id = int(id_str)
    except Exception:
        return
    with td.orm_session():
        with qcounter("stocktype_change"):
            items = joinedload(StockType.items)
            stockline = items.joinedload(StockItem.stockline)
            sos = stockline.joinedload(StockLine.stockonsale)
            st = td.s.query(StockType)\
                     .options(items,
                              stockline,
                              sos,
                              joinedload(StockType.meta),
                              undefer(StockType.total_remaining),
                              undefer(StockType.total),
                              items.undefer(StockItem.remaining))\
                     .get(id)
            if not st:
                delete(f"stocktype/{id}")
                return
            publish(stocktype_to_dict(st))
            # All items of this stocktype will have changed as well
            for si in st.items:
                publish(stockitem_to_dict(si))
                # If the item is connected to a stockline, that stockline will
                # also have changed
                if si.stockline:
                    publish(stockline_to_dict(si.stockline))


def notify_stockitem_change(id_str):
    try:
        id = int(id_str)
    except Exception:
        return
    with td.orm_session():
        with qcounter("stockitem_change"):
            st = joinedload(StockItem.stocktype)
            sl = joinedload(StockItem.stockline)
            si = td.s.query(StockItem)\
                     .options(undefer(StockItem.remaining),
                              st,
                              sl,
                              sl.joinedload(StockLine.stockonsale),
                              st.undefer(StockType.total_remaining),
                              st.undefer(StockType.total))\
                     .get(id)
            if not si:
                delete(f"stockitem/{id}")
                return
            publish(stockitem_to_dict(si))
            # The item being connected to or disconnected from a
            # stockline is already covered by stockline_change
            # notifications. Updates to amount remaining are not;
            # publish an update here and live with duplicate
            # notifications.
            if si.stockline:
                publish(stockline_to_dict(si.stockline))

            # Updates to amounts remaining and stockline connections must
            # also be published for the item's stocktype
            publish(stocktype_to_dict(si.stocktype))

            publish_totals_by_unit()


def background_minute():
    mainloop.add_timeout(60.0, background_minute)
    with td.orm_session():
        publish_cup_reuse_count()


class Command(BaseCommand):
    help = 'Forward events from the till database to redis'

    def add_arguments(self, parser):
        parser.add_argument(
            '--count-queries', action='store_true', default=False,
            help="Output number of queries per event")
        parser.add_argument(
            '--show-queries', action='store_true', default=False,
            help="Output SQL used for all queries")
        parser.add_argument(
            '--redis-host', action='store', default='localhost',
            help="Host to use to access redis")
        parser.add_argument(
            '--redis-port', action='store', default=6379, type=int,
            help="Port to use to access redis")

    def handle(self, *args, **options):
        global qcounter_enabled, show_queries, rcon, mainloop
        qcounter_enabled = options["count_queries"] or options["show_queries"]
        show_queries = options["show_queries"]

        with tillsession() as s:
            url = str(s.get_bind().url)
        td.init(url)
        mainloop = event.SelectorsMainLoop()
        listener = listen.db_listener(mainloop, td.engine)
        rcon = redis.Redis(
            host=options["redis_host"],
            port=options["redis_port"],
            decode_responses=True)

        # Start listening
        listener.listen_for("stockline_change", notify_stockline_change)
        listener.listen_for("stocktype_change", notify_stocktype_change)
        listener.listen_for("stockitem_change", notify_stockitem_change)

        # Preload redis with the current state of all the objects we can publish

        with td.orm_session():
            with qcounter("init"):
                stocktypes = td.s.query(StockType)\
                                 .options(joinedload(StockType.unit))\
                                 .options(joinedload(StockType.meta))\
                                 .options(undefer(StockType.total),
                                          undefer(StockType.total_remaining))\
                                 .all()
                stockitems = td.s.query(StockItem)\
                                 .options(undefer(StockItem.remaining))\
                                 .all()
                stocklines = td.s.query(StockLine)\
                                 .options(joinedload(StockLine.stockonsale))\
                                 .all()

                for sl in stocklines:
                    publish(stockline_to_dict(sl))
                for st in stocktypes:
                    publish(stocktype_to_dict(st))
                for si in stockitems:
                    publish(stockitem_to_dict(si))

                publish_totals_by_unit()

        background_minute()

        # Notify systemd that startup is complete
        sdnotify.SystemdNotifier().notify("READY=1")

        # Loop forever (or until the database or redis is restarted)
        while True:
            mainloop.iterate()
