import datetime
import json
from decimal import Decimal
from hmac import compare_digest

import sqlalchemy
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from quicktill.models import (
    LogEntry,
    Session,
    StockLine,
    StockOut,
    StockType,
    Transaction,
    TransactionMeta,
    Transline,
    User,
    max_quantity,
    zero,
)
from sqlalchemy.orm import joinedload

from .tilldb import tillsession


order_meta_key = "emf:kiosk-order"
default_timeout = datetime.timedelta(minutes=15)


class KioskOrderError(Exception):
    status_code = 400
    code = "kiosk-order-error"

    def __init__(self, message, *, stockline_id=None):
        super().__init__(message)
        self.message = message
        self.stockline_id = stockline_id

    def as_dict(self):
        d = {
            "error": self.code,
            "message": self.message,
        }
        if self.stockline_id is not None:
            d["stockline_id"] = self.stockline_id
        return d


class NoActiveSession(KioskOrderError):
    status_code = 409
    code = "no-active-session"


class UnknownStockLine(KioskOrderError):
    status_code = 404
    code = "unknown-stockline"


class WrongLocation(KioskOrderError):
    status_code = 403
    code = "wrong-location"


class UnsupportedStockLine(KioskOrderError):
    status_code = 409
    code = "unsupported-stockline"


class NoStockOnSale(KioskOrderError):
    status_code = 409
    code = "no-stock-on-sale"


class PriceNotSet(KioskOrderError):
    status_code = 409
    code = "price-not-set"


class InsufficientStock(KioskOrderError):
    status_code = 409
    code = "insufficient-stock"

    def __init__(self, message, *, stockline_id=None, requested=None,
                 available=None):
        super().__init__(message, stockline_id=stockline_id)
        self.requested = requested
        self.available = available

    def as_dict(self):
        d = super().as_dict()
        if self.requested is not None:
            d["requested"] = str(self.requested)
        if self.available is not None:
            d["available"] = str(self.available)
        return d


class InvalidQuantity(KioskOrderError):
    code = "invalid-quantity"


class InvalidStockLine(KioskOrderError):
    code = "invalid-stockline"


def _money(value):
    return str(value.quantize(Decimal("0.01")))


def _timestamp(dt):
    return dt.replace(microsecond=0).isoformat()


def _parse_timestamp(value):
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _read_meta(trans):
    meta = trans.meta.get(order_meta_key)
    if not meta:
        return None
    try:
        return json.loads(meta.value)
    except json.JSONDecodeError:
        return None


def _set_meta(trans, value):
    trans.set_meta(order_meta_key, json.dumps(value, sort_keys=True))


def _stockline_options():
    return [
        joinedload(StockLine.stocktype).joinedload(StockType.unit),
        joinedload(StockLine.stocktype).undefer(StockType.remaining),
    ]


def _read_qty(value):
    try:
        qty = int(value)
    except (TypeError, ValueError):
        raise InvalidQuantity("Quantity must be a positive integer.")
    if qty <= 0:
        raise InvalidQuantity("Quantity must be a positive integer.")
    return qty


def _read_stockline_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise InvalidStockLine("stockline_id must be an integer.")


def _load_line(session, stockline_id, location):
    line = session.query(StockLine)\
        .filter(StockLine.id == stockline_id)\
        .options(*_stockline_options())\
        .with_for_update(of=StockLine)\
        .one_or_none()
    if not line:
        raise UnknownStockLine(
            f"Stock line {stockline_id} does not exist.",
            stockline_id=stockline_id)
    if line.location != location:
        raise WrongLocation(
            f"Stock line {stockline_id} is not in location {location}.",
            stockline_id=stockline_id)
    if line.linetype != "continuous":
        raise UnsupportedStockLine(
            f"{line.name} is not a continuous stock line.",
            stockline_id=stockline_id)
    if not line.stocktype:
        raise NoStockOnSale(
            f"No stock is registered for {line.name}.",
            stockline_id=stockline_id)
    return line


def _plan_sale(line, qty):
    st = line.stocktype
    if st.saleprice is None:
        raise PriceNotSet(
            f"{st} does not have a sale price set.",
            stockline_id=line.id)

    total_stock_qty = Decimal(qty) * st.unit.base_units_per_sale_unit
    if line.remaining < total_stock_qty:
        raise InsufficientStock(
            f"There is not enough stock on sale for {line.name}.",
            stockline_id=line.id,
            requested=total_stock_qty,
            available=line.remaining)

    sell, unallocated, _remaining = line.calculate_sale(total_stock_qty)
    if unallocated > zero or not sell:
        raise InsufficientStock(
            f"There is not enough stock on sale for {line.name}.",
            stockline_id=line.id,
            requested=total_stock_qty,
            available=line.remaining)

    for _stockitem, stock_qty in sell:
        if stock_qty > max_quantity:
            raise InvalidQuantity(
                f"Quantity is too large for {line.name}.",
                stockline_id=line.id)

    return {
        "line": line,
        "stocktype": st,
        "qty": qty,
        "stock_qty": total_stock_qty,
        "sell": sell,
        "description": f"{st} {st.unit.sale_unit_name}",
        "price": st.saleprice,
    }


def _order_lines(trans):
    lines = []
    total = zero
    for tl in trans.lines:
        if tl.amount == zero:
            continue
        line_total = tl.amount * tl.items
        total += line_total
        lines.append({
            "description": tl.text,
            "quantity": tl.items,
            "unit_price": _money(tl.amount),
            "line_total": _money(line_total),
        })
    return lines, total


def _order_response(trans, meta, *, created):
    lines, total = _order_lines(trans)
    order_ref = meta["order_ref"]
    order_prefix = meta["order_prefix"]
    return {
        "order_ref": order_ref,
        "order_name": f"{order_prefix} {order_ref}",
        "order_prefix": order_prefix,
        "location": meta["location"],
        "transaction_id": trans.id,
        "created": created,
        "created_at": meta["created_at"],
        "expires_at": meta["expires_at"],
        "idempotency_key": meta.get("idempotency_key"),
        "status": "accepted",
        "total": _money(total),
        "lines": lines,
        "slip": {
            "title": f"{order_prefix} order {order_ref}",
            "created_at": meta["created_at"],
            "expires_at": meta["expires_at"],
            "unpaid": True,
            "total": _money(total),
            "lines": lines,
        },
    }


def _existing_order(session, idempotency_key):
    if not idempotency_key:
        return None, None
    rows = session.query(TransactionMeta)\
        .filter(TransactionMeta.key == order_meta_key)\
        .options(joinedload(TransactionMeta.transaction))\
        .all()
    for row in rows:
        meta = _read_meta(row.transaction)
        if meta and meta.get("idempotency_key") == idempotency_key:
            return row.transaction, meta
    return None, None


def _new_order_ref(_session):
    from .models import KioskOrderRef
    ref = KioskOrderRef.objects.create()
    return f"{ref.id:04d}"


def _fallback_log_user(session, user):
    if user:
        return user
    return session.query(User)\
        .filter(User.enabled == True)\
        .order_by(User.superuser.desc(), User.id)\
        .first() or session.query(User).order_by(User.id).first()


def place_order(session, *, location, items, order_prefix="Kiosk",
                source="kiosk", idempotency_key=None, user=None, now=None,
                timeout=default_timeout):
    existing, meta = _existing_order(session, idempotency_key)
    if existing:
        return _order_response(existing, meta, created=False)

    current_session = Session.current(session)
    if current_session is None:
        raise NoActiveSession("No till session is active.")

    if not items:
        raise InvalidQuantity("Order must contain at least one item.")

    quantities = {}
    for item in items:
        stockline_id = _read_stockline_id(item.get("stockline_id"))
        qty = _read_qty(item.get("qty", 1))
        quantities[stockline_id] = quantities.get(stockline_id, 0) + qty

    plans = []
    for stockline_id, qty in sorted(quantities.items()):
        line = _load_line(session, stockline_id, location)
        plans.append(_plan_sale(line, qty))

    now = now or datetime.datetime.now()
    expires_at = now + timeout
    order_ref = _new_order_ref(session)
    order_name = f"{order_prefix} {order_ref}"
    meta = {
        "order_ref": order_ref,
        "order_prefix": order_prefix,
        "location": location,
        "created_at": _timestamp(now),
        "expires_at": _timestamp(expires_at),
    }
    if idempotency_key:
        meta["idempotency_key"] = idempotency_key

    trans = Transaction(session=current_session, notes=order_name)
    session.add(trans)
    session.flush()

    session.add(Transline(
        transaction=trans,
        items=1,
        amount=zero,
        department=plans[0]["stocktype"].department,
        transcode='S',
        text=f"{order_prefix} order {order_ref}:",
        user=user,
        source=source,
        protected=True))

    for plan in plans:
        tl = Transline(
            transaction=trans,
            items=plan["qty"],
            amount=plan["price"],
            department=plan["stocktype"].department,
            transcode='S',
            text=plan["description"],
            user=user,
            source=source,
            protected=True)
        session.add(tl)
        for stockitem, stock_qty in plan["sell"]:
            session.add(StockOut(
                transline=tl,
                stockitem=stockitem,
                qty=stock_qty,
                removecode_id="sold"))
            session.expire(
                stockitem,
                ['used', 'sold', 'remaining', 'firstsale', 'lastsale'])

    _set_meta(trans, meta)
    session.flush()
    return _order_response(trans, meta, created=True)


def expire_orders(session, *, location=None, now=None, source="kiosk-expiry",
                  user=None):
    now = now or datetime.datetime.now()
    loguser = _fallback_log_user(session, user)
    if loguser is None:
        return []

    rows = session.query(TransactionMeta)\
        .filter(TransactionMeta.key == order_meta_key)\
        .join(Transaction)\
        .filter(Transaction.closed == False)\
        .options(joinedload(TransactionMeta.transaction)
                 .joinedload(Transaction.payments),
                 joinedload(TransactionMeta.transaction)
                 .joinedload(Transaction.meta))\
        .all()

    expired = []
    for row in rows:
        trans = row.transaction
        meta = _read_meta(trans)
        if not meta:
            continue
        if location is not None and meta.get("location") != location:
            continue
        expires_at = _parse_timestamp(meta.get("expires_at"))
        if expires_at is None or expires_at > now:
            continue
        if trans.payments:
            continue

        expired.append({
            "transaction_id": trans.id,
            "order_ref": meta.get("order_ref"),
            "order_name": trans.notes,
        })
        session.add(LogEntry(
            source=source,
            loguser=loguser,
            description=(
                f"Expired kiosk order {trans.notes} "
                f"(transaction {trans.id})")))
        session.delete(trans)

    session.flush()
    return expired


def list_orders(session, *, location, prefix=None):
    """Return active kiosk orders for a location and order prefix.

    Returns all unpaid orders (closed=False) plus all paid orders
    (closed=True, any session) that match the given prefix. Scoping by
    prefix rather than session means paid orders survive a till session
    restart during service. Expired unpaid orders are already deleted by
    expire_orders, so nothing stale leaks in from the unpaid side.
    """
    rows = session.query(TransactionMeta)\
        .filter(TransactionMeta.key == order_meta_key)\
        .join(Transaction)\
        .options(
            joinedload(TransactionMeta.transaction)
            .joinedload(Transaction.lines),
            joinedload(TransactionMeta.transaction)
            .joinedload(Transaction.meta),
        )\
        .all()

    orders = []
    for row in rows:
        trans = row.transaction
        meta = _read_meta(trans)
        if not meta:
            continue
        if meta.get("location") != location:
            continue
        if prefix is not None and meta.get("order_prefix") != prefix:
            continue
        lines, total = _order_lines(trans)
        orders.append({
            "order_ref": meta["order_ref"],
            "order_name": f"{meta.get('order_prefix', 'Kiosk')} {meta['order_ref']}",
            "transaction_id": trans.id,
            "created_at": meta.get("created_at"),
            "expires_at": meta.get("expires_at"),
            "total": _money(total),
            "paid": bool(trans.closed),
            "lines": lines,
        })

    return orders


def _json_error(status, code, message, **kwargs):
    d = {
        "error": code,
        "message": message,
    }
    d.update(kwargs)
    return JsonResponse(d, status=status)


def _token_config():
    return getattr(settings, "EMF_KIOSK_ORDER_TOKENS", {})


def _bearer_token(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _normalise_token_entry(entry):
    if isinstance(entry, str):
        return {
            "locations": [entry],
            "order_prefix": "Kiosk",
            "source": "kiosk",
        }
    locations = entry.get("locations", entry.get("location", []))
    if isinstance(locations, str):
        locations = [locations]
    return {
        "locations": list(locations),
        "order_prefix": entry.get("order_prefix", "Kiosk"),
        "source": entry.get("source", "kiosk"),
        "user": entry.get("user"),
    }


def _authenticate(request, location):
    configured = _token_config()
    if not configured:
        return None, _json_error(
            503, "kiosk-api-not-configured",
            "Kiosk API tokens have not been configured.")

    supplied = _bearer_token(request)
    if not supplied:
        return None, _json_error(
            401, "missing-token",
            "Supply a bearer token in the Authorization header.")

    for token, entry in configured.items():
        if compare_digest(str(token), supplied):
            auth = _normalise_token_entry(entry)
            if location not in auth["locations"]:
                return None, _json_error(
                    403, "location-not-allowed",
                    "This token is not allowed to access that location.")
            return auth, None

    return None, _json_error(401, "invalid-token", "Bearer token not valid.")


def _request_json(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return None


def _auth_user(session, username):
    if not username:
        return None
    return session.query(User).filter(User.webuser == username).one_or_none()


def _orders_get(request):
    location = request.GET.get("location")
    if not location:
        return _json_error(400, "missing-location", "Supply ?location=<name>.")
    auth, response = _authenticate(request, location)
    if response:
        return response
    prefix = request.GET.get("prefix") or None
    with tillsession() as s:
        try:
            orders = list_orders(s, location=location, prefix=prefix)
            return JsonResponse({"location": location, "orders": orders})
        except sqlalchemy.exc.OperationalError as e:
            return _json_error(503, "database-error", str(e))


@csrf_exempt
def orders(request):
    if request.method == "GET":
        return _orders_get(request)
    if request.method != "POST":
        return _json_error(405, "method-not-allowed", "Use GET or POST.")

    payload = _request_json(request)
    if payload is None:
        return _json_error(400, "invalid-json", "Request body is not JSON.")

    location = payload.get("location")
    if not location:
        return _json_error(400, "missing-location", "Location is required.")

    auth, response = _authenticate(request, location)
    if response:
        return response

    with tillsession() as s:
        try:
            user = _auth_user(s, auth.get("user"))
            expired = expire_orders(
                s,
                location=location,
                source=f"{auth['source']}-expiry",
                user=user)
            result = place_order(
                s,
                location=location,
                items=payload.get("items", []),
                order_prefix=auth["order_prefix"],
                source=auth["source"],
                idempotency_key=payload.get("idempotency_key"),
                user=user)
            result["expired_orders"] = expired
            s.commit()
            return JsonResponse(
                result, status=201 if result["created"] else 200)
        except KioskOrderError as e:
            s.rollback()
            return JsonResponse(e.as_dict(), status=e.status_code)
        except sqlalchemy.exc.OperationalError as e:
            s.rollback()
            return _json_error(503, "database-error", str(e))


@csrf_exempt
def expire(request):
    if request.method != "POST":
        return _json_error(405, "method-not-allowed", "Use POST.")

    payload = _request_json(request)
    if payload is None:
        return _json_error(400, "invalid-json", "Request body is not JSON.")

    location = payload.get("location")
    if not location:
        return _json_error(400, "missing-location", "Location is required.")

    auth, response = _authenticate(request, location)
    if response:
        return response

    with tillsession() as s:
        user = _auth_user(s, auth.get("user"))
        expired = expire_orders(
            s,
            location=location,
            source=f"{auth['source']}-expiry",
            user=user)
        s.commit()
        return JsonResponse({
            "location": location,
            "expired_orders": expired,
        })
