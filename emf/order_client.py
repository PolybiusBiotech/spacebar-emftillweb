import datetime
import hashlib
import hmac as _hmac
import json
from decimal import Decimal
from hmac import compare_digest

import sqlalchemy
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from quicktill.models import (
    LogEntry,
    RefusalsLog,
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
barcode_prefix = "KIOSK:"


def _barcode_secret():
    return getattr(settings, "EMF_KIOSK_BARCODE_SECRET", "")


def _checkdigits(trans_id):
    secret = _barcode_secret().encode()
    msg = str(trans_id).encode()
    h = _hmac.new(secret, msg, hashlib.sha1) if secret else hashlib.sha1(msg)
    return str(int(h.hexdigest(), 16))[-3:]


def _order_barcode(trans_id):
    return f"{barcode_prefix}{trans_id}{_checkdigits(trans_id)}"


def _verify_barcode(barcode):
    """Return transaction_id if barcode HMAC is valid, else None."""
    if not barcode.startswith(barcode_prefix):
        return None
    rest = barcode[len(barcode_prefix):]   # "{trans_id}{3-digit check}"
    if len(rest) < 4:
        return None
    check, trans_id_str = rest[-3:], rest[:-3]
    try:
        trans_id = int(trans_id_str)
    except ValueError:
        return None
    if not compare_digest(check, _checkdigits(trans_id)):
        return None
    return trans_id


def _is_soft(department):
    """True when the department has a capped ABV ≤ 0.5% — no age check needed."""
    return department.maxabv is not None and department.maxabv <= Decimal("0.5")


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


class TooManyItems(KioskOrderError):
    status_code = 400
    code = "too-many-items"


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
    """Return a transaction's decoded kiosk-order metadata, or None.

    None means this is not a kiosk order (the transaction has no kiosk meta
    key) or its stored metadata isn't valid JSON. Callers treat None as
    "not a kiosk order" — e.g. a 404 on the order endpoints.
    """
    entry = trans.meta.get(order_meta_key)
    if not entry:
        return None
    try:
        return json.loads(entry.value)
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
    barcode = _order_barcode(trans.id)
    return {
        "order_ref": order_ref,
        "barcode": barcode,
        "location": meta["location"],
        "transaction_id": trans.id,
        "created": created,
        "created_at": meta["created_at"],
        "expires_at": meta["expires_at"],
        "soft_only": meta.get("soft_only", False),
        "status": "accepted",
        "total": _money(total),
        "lines": lines,
        "slip": {
            "title": f"Order {order_ref}",
            "created_at": meta["created_at"],
            "expires_at": meta["expires_at"],
            "unpaid": True,
            "total": _money(total),
            "lines": lines,
        },
    }


def _fallback_log_user(session, user):
    if user:
        return user
    return session.query(User)\
        .filter(User.enabled == True)\
        .order_by(User.superuser.desc(), User.id)\
        .first() or session.query(User).order_by(User.id).first()


def place_order(session, *, location, items, source="kiosk", user=None,
                now=None, timeout=default_timeout, max_items=None):
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

    if max_items is not None:
        total_qty = sum(quantities.values())
        if total_qty > max_items:
            raise TooManyItems(
                f"Orders via this token are limited to {max_items} item(s). "
                f"Requested {total_qty}.")

    plans = []
    for stockline_id, qty in sorted(quantities.items()):
        line = _load_line(session, stockline_id, location)
        plans.append(_plan_sale(line, qty))

    soft_only = all(_is_soft(p["stocktype"].department) for p in plans)

    now = now or datetime.datetime.now()
    expires_at = now + timeout

    trans = Transaction(session=current_session, notes="Kiosk order")
    session.add(trans)
    session.flush()

    order_ref = str(trans.id)
    meta = {
        "order_ref": order_ref,
        "location": location,
        "source": source,
        "created_at": _timestamp(now),
        "expires_at": _timestamp(expires_at),
        "soft_only": soft_only,
    }

    trans.notes = f"Kiosk order {order_ref}"
    session.add(Transline(
        transaction=trans,
        items=1,
        amount=zero,
        department=plans[0]["stocktype"].department,
        transcode='S',
        text=f"Kiosk order {order_ref}:",
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
        if trans.user is not None:
            # Transaction is active in a register — skip and retry next pass.
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


def mark_collected(session, *, order_ref, location, source="kiosk", user=None):
    """Mark a kiosk order as collected — removes it from future poll results."""
    trans = session.get(Transaction, int(order_ref))
    if trans is None:
        raise KioskOrderError(404, "not-found", f"Order {order_ref} not found.")
    meta = _read_meta(trans)
    if meta is None or meta.get("location") != location:
        raise KioskOrderError(404, "not-found", f"Order {order_ref} not found.")
    if meta.get("collected"):
        return
    meta["collected"] = True
    _set_meta(trans, meta)
    loguser = _fallback_log_user(session, user)
    if loguser:
        session.add(LogEntry(
            source=source,
            loguser=loguser,
            description=f"Kiosk order {order_ref} marked collected"))
    session.flush()


def mark_rejected(session, *, order_ref, location, source="kiosk", user=None,
                  terminal="kiosk"):
    """Mark a kiosk order as ID-rejected — blocks re-scan and logs to refusals."""
    trans = session.get(Transaction, int(order_ref))
    if trans is None:
        raise KioskOrderError(404, "not-found", f"Order {order_ref} not found.")
    meta = _read_meta(trans)
    if meta is None or meta.get("location") != location:
        raise KioskOrderError(404, "not-found", f"Order {order_ref} not found.")
    if meta.get("rejected"):
        return
    meta["rejected"] = True
    _set_meta(trans, meta)
    loguser = _fallback_log_user(session, user)
    if loguser:
        session.add(RefusalsLog(
            user=loguser,
            terminal=terminal,
            details=f"ID rejected via kiosk (order {order_ref})"))
    session.flush()


def list_orders(session, *, location):
    """Return active kiosk orders for a location.

    Returns all unpaid orders (closed=False) plus all paid orders
    (closed=True, any session) for the location. Scoping by location
    rather than session means paid orders survive a till session restart
    during service. Expired unpaid orders are already deleted by
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
        if meta.get("collected"):
            continue
        lines, total = _order_lines(trans)
        orders.append({
            "order_ref": meta["order_ref"],
            "order_name": meta["order_ref"],
            "transaction_id": trans.id,
            "created_at": meta.get("created_at"),
            "expires_at": meta.get("expires_at"),
            "soft_only": meta.get("soft_only", False),
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
    return getattr(settings, "EMF_KIOSK_ORDER_TOKEN")


def _bearer_token(request):
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _service_user():
    return getattr(settings, "EMF_KIOSK_USER", "")


def _cancel_mode():
    """Order cancellation behaviour: "delete" (default) removes the record;
    "void" keeps it and adds voiding lines that show on the till void report."""
    return getattr(settings, "EMF_KIOSK_CANCEL_MODE", "delete")


def _kiosk_identity():
    """Return the identity of the kiosk service for logging and auditing."""
    return {
        "source": "kiosk",
        "user": _service_user(),
        "timeout": default_timeout,
        "max_items": None,
    }


def _check_token(request):
    """Validate the shared kiosk bearer token.

    Returns an error JsonResponse if the token is missing or invalid, or
    None when the request is authenticated.
    """
    configured = _token_config()
    if not configured:
        return _json_error(
            503, "kiosk-api-not-configured",
            "Kiosk API token has not been configured.")
    token = _bearer_token(request)
    if not token:
        return _json_error(
            401, "missing-token",
            "Missing bearer token in Authorization header.")
    if not compare_digest(token, str(configured)):
        return _json_error(
            401, "invalid-token",
            "Invalid bearer token in Authorization header")
    return None


def _authenticate(request):
    """Authenticate a kiosk request via the shared bearer token.

    With a single, unscoped token there is no per-location gating, so every
    endpoint shares this one check.
    """
    error = _check_token(request)
    if error:
        return None, error
    return _kiosk_identity(), None


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
    auth, response = _authenticate(request)
    if response:
        return response
    with tillsession() as s:
        try:
            orders = list_orders(s, location=location)
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

    auth, response = _authenticate(request)
    if response:
        return response

    with tillsession() as s:
        try:
            user = _auth_user(s, auth.get("user"))
            result = place_order(
                s,
                location=location,
                items=payload.get("items", []),
                source=auth["source"],
                user=user,
                timeout=auth["timeout"],
                max_items=auth.get("max_items"))
            s.commit()
            return JsonResponse(result, status=201)
        except KioskOrderError as e:
            s.rollback()
            return JsonResponse(e.as_dict(), status=e.status_code)
        except sqlalchemy.exc.OperationalError as e:
            s.rollback()
            return _json_error(503, "database-error", str(e))


@csrf_exempt
def order_detail(request, order_ref):
    """Single-order resource: GET retrieves it, DELETE cancels it."""
    if request.method not in ("GET", "DELETE"):
        return _json_error(405, "method-not-allowed", "Use GET or DELETE.")

    auth, response = _authenticate(request)
    if response:
        return response

    if request.method == "GET":
        return _order_get_one(request, order_ref, auth)
    return _order_cancel(request, order_ref, auth)


def _order_get_one(request, order_ref, auth):
    """Retrieve a single kiosk order by its reference.

    Token auth only — reading an order is low-stakes, so unlike cancel this
    does not require the receipt barcode.
    """
    try:
        order_ref = int(order_ref)
    except ValueError:
        return _json_error(404, "not-found", "Order not found.")

    with tillsession() as s:
        try:
            trans = s.query(Transaction)\
                .filter(Transaction.id == order_ref)\
                .options(joinedload(Transaction.meta))\
                .one_or_none()

            if not trans:
                return _json_error(404, "not-found", "Order not found.")

            meta = _read_meta(trans)
            if not meta:
                return _json_error(404, "not-found", "Order not found.")

            return JsonResponse(_order_response(trans, meta, created=False))

        except sqlalchemy.exc.OperationalError as e:
            s.rollback()
            return _json_error(503, "database-error", str(e))


def _void_transaction(session, trans, user, source):
    """Void every line of an unpaid order instead of deleting it.

    The voiding lines (transcode 'V') appear on the till's void report, and the
    now zero-balance transaction is closed. Selected by
    EMF_KIOSK_CANCEL_MODE="void".
    """
    # Snapshot the lines first: tl.void() adds a new Transline to trans.lines,
    # so iterating trans.lines directly would mutate the collection mid-loop.
    voidlines = [tl.void(trans, user, source) for tl in list(trans.lines)]
    session.add_all([v for v in voidlines if v is not None])
    trans.closed = True


def _order_cancel(request, order_ref, auth):
    """Cancel an unpaid kiosk order.

    The URL identifies which order; the Order-Barcode header proves the caller
    holds its receipt. The two must refer to the same order.
    """
    barcode = request.headers.get("Order-Barcode", "")

    trans_id = _verify_barcode(barcode)
    if trans_id is None:
        return _json_error(403, "bad-barcode", "Barcode checksum is invalid.")

    if str(trans_id) != order_ref:
        return _json_error(403, "bad-barcode",
                           "Order reference does not match barcode.")

    with tillsession() as s:
        try:
            trans = s.query(Transaction)\
                .filter(Transaction.id == trans_id)\
                .options(
                    joinedload(Transaction.meta),
                    joinedload(Transaction.payments),
                )\
                .with_for_update()\
                .one_or_none()

            if not trans:
                return _json_error(404, "not-found", "Order not found.")

            meta = _read_meta(trans)
            if not meta:
                return _json_error(404, "not-found", "Order not found.")

            if trans.closed or trans.payments:
                return _json_error(409, "already-paid",
                                   "Order has already been paid and cannot be cancelled.")

            if trans.user is not None:
                return _json_error(409, "order-in-use",
                                   "Order is currently being processed at the till.")

            loguser = _fallback_log_user(s, _auth_user(s, auth.get("user")))
            if loguser:
                s.add(LogEntry(
                    source=auth["source"],
                    loguser=loguser,
                    description=(
                        f"Cancelled kiosk order {trans.notes} "
                        f"(transaction {trans.id})")))

            if _cancel_mode() == "void":
                _void_transaction(s, trans, loguser, auth["source"])
            else:
                s.delete(trans)
            s.flush()
            s.commit()
            return JsonResponse({"ok": True, "order_ref": order_ref})

        except sqlalchemy.exc.OperationalError as e:
            s.rollback()
            return _json_error(503, "database-error", str(e))


@csrf_exempt
def collect(request, order_ref):
    if request.method != "POST":
        return _json_error(405, "method-not-allowed", "Use POST.")

    auth, response = _authenticate(request)
    if response:
        return response

    location = request.GET.get("location")
    if not location:
        return _json_error(400, "missing-location", "Supply ?location=<name>.")

    with tillsession() as s:
        try:
            user = _auth_user(s, auth.get("user"))
            mark_collected(
                s,
                order_ref=order_ref,
                location=location,
                source=auth["source"],
                user=user)
            s.commit()
            return JsonResponse({"ok": True, "order_ref": order_ref})
        except KioskOrderError as e:
            s.rollback()
            return JsonResponse(e.as_dict(), status=e.status_code)
        except (ValueError, sqlalchemy.exc.OperationalError) as e:
            s.rollback()
            return _json_error(400, "bad-request", str(e))


@csrf_exempt
def reject(request, order_ref):
    if request.method != "POST":
        return _json_error(405, "method-not-allowed", "Use POST.")

    auth, response = _authenticate(request)
    if response:
        return response

    location = request.GET.get("location")
    if not location:
        return _json_error(400, "missing-location", "Supply ?location=<name>.")

    with tillsession() as s:
        try:
            user = _auth_user(s, auth.get("user"))
            mark_rejected(
                s,
                order_ref=order_ref,
                location=location,
                source=auth["source"],
                user=user,
                terminal=auth["source"])
            s.commit()
            return JsonResponse({"ok": True, "order_ref": order_ref})
        except KioskOrderError as e:
            s.rollback()
            return JsonResponse(e.as_dict(), status=e.status_code)
        except (ValueError, sqlalchemy.exc.OperationalError) as e:
            s.rollback()
            return _json_error(400, "bad-request", str(e))
