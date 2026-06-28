"""Tests for emf.order_client — pure functions and view paths.

DB-touching logic (place_order, list_orders, cancel, collect/reject) is
exercised by mocking tillsession(). Error paths that need real data (the
stock validation in place_order) are left to integration tests against a
quicktill database; see the developer docs.
"""

import datetime
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase, override_settings

from emf.kiosk import (
    _authenticate,
    _bearer_token,
    _checkdigits,
    _order_barcode,
    _verify_barcode,
    OrderNotFound,
    barcode_prefix,
    mark_collected,
    mark_rejected,
    order_detail,
    orders,
)


SECRET = "test-barcode-secret-xyz"
TOKEN = "test-bearer-token-abc"
LOCATION = "Spacebar"
USER = "kiosk"


# ---------------------------------------------------------------------------
# HMAC / barcode
# ---------------------------------------------------------------------------

class CheckdigitsTests(TestCase):
    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_deterministic(self):
        self.assertEqual(_checkdigits(42), _checkdigits(42))

    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_three_decimal_digits(self):
        d = _checkdigits(9574)
        self.assertEqual(len(d), 3)
        self.assertTrue(d.isdigit())

    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_varies_by_id(self):
        # Two very different IDs should produce different check digits (not
        # guaranteed in general but holds for these specific values).
        d1 = _checkdigits(1)
        d9 = _checkdigits(9999999)
        self.assertNotEqual(d1, d9)

    @override_settings(EMF_KIOSK_BARCODE_SECRET="")
    def test_no_secret_still_returns_three_digits(self):
        d = _checkdigits(1)
        self.assertEqual(len(d), 3)
        self.assertTrue(d.isdigit())


class OrderBarcodeTests(TestCase):
    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_starts_with_prefix(self):
        self.assertTrue(_order_barcode(9574).startswith(barcode_prefix))

    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_contains_trans_id(self):
        barcode = _order_barcode(9574)
        rest = barcode[len(barcode_prefix):]
        self.assertTrue(rest.startswith("9574"))

    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_check_digits_appended(self):
        barcode = _order_barcode(9574)
        rest = barcode[len(barcode_prefix):]
        check = rest[4:]  # 4 = len("9574")
        self.assertEqual(len(check), 3)
        self.assertTrue(check.isdigit())


class VerifyBarcodeTests(TestCase):
    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_valid_roundtrip(self):
        barcode = _order_barcode(9574)
        self.assertEqual(_verify_barcode(barcode), 9574)

    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_corrupted_check_rejected(self):
        barcode = _order_barcode(9574)
        last = barcode[-1]
        bad = barcode[:-1] + ("0" if last != "0" else "1")
        self.assertIsNone(_verify_barcode(bad))

    def test_wrong_prefix_rejected(self):
        self.assertIsNone(_verify_barcode("ORDER:9574123"))

    def test_too_short_rejected(self):
        # Need at least 4 chars after prefix (trans_id + 3 check digits)
        self.assertIsNone(_verify_barcode(barcode_prefix + "12"))

    def test_non_integer_trans_id_rejected(self):
        self.assertIsNone(_verify_barcode(barcode_prefix + "abcdef"))

    def test_empty_rejected(self):
        self.assertIsNone(_verify_barcode(""))


# ---------------------------------------------------------------------------
# Bearer token parsing
# ---------------------------------------------------------------------------

class BearerTokenTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_present(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION="Bearer mytoken123")
        self.assertEqual(_bearer_token(req), "mytoken123")

    def test_absent(self):
        req = self.factory.get("/")
        self.assertIsNone(_bearer_token(req))

    def test_wrong_scheme(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION="Basic mytoken123")
        self.assertIsNone(_bearer_token(req))

    def test_bearer_only_no_token(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION="Bearer ")
        self.assertIsNone(_bearer_token(req))

    def test_case_insensitive_scheme(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION="BEARER mytoken123")
        self.assertEqual(_bearer_token(req), "mytoken123")


# ---------------------------------------------------------------------------
# _authenticate
# ---------------------------------------------------------------------------

class AuthenticateTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(EMF_KIOSK_ORDER_TOKEN="")
    def test_no_tokens_configured(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        auth, resp = _authenticate(req)
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 503)
        data = json.loads(resp.content)
        self.assertEqual(data["error"], "kiosk-api-not-configured")

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_missing_bearer(self):
        req = self.factory.get("/")
        auth, resp = _authenticate(req)
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 401)
        data = json.loads(resp.content)
        self.assertEqual(data["error"], "missing-token")

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_invalid_token(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION="Bearer wrong-token")
        auth, resp = _authenticate(req)
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 401)
        data = json.loads(resp.content)
        self.assertEqual(data["error"], "invalid-token")

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_valid(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        auth, resp = _authenticate(req)
        self.assertIsNotNone(auth)
        self.assertIsNone(resp)
        self.assertEqual(auth["source"], "kiosk")


# ---------------------------------------------------------------------------
# orders view
# ---------------------------------------------------------------------------

def _mock_tillsession(return_value=None):
    """Return a patch for tillsession() that yields return_value."""
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(
        return_value=return_value or MagicMock())
    mock_session.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def fake_tillsession():
        yield mock_session.__enter__.return_value

    return patch("emf.kiosk.tillsession", fake_tillsession)


class OrdersViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_unsupported_method(self):
        req = self.factory.delete("/api/kiosk/orders")
        resp = orders(req)
        self.assertEqual(resp.status_code, 405)

    # --- GET ---

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_get_missing_location(self):
        req = self.factory.get("/api/kiosk/orders",
                               HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = orders(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content)["error"], "missing-location")

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_get_no_auth(self):
        req = self.factory.get("/api/kiosk/orders", {"location": LOCATION})
        resp = orders(req)
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_get_success(self):
        with _mock_tillsession():
            with patch("emf.kiosk.list_orders",
                       return_value=[]) as mock_list:
                req = self.factory.get("/api/kiosk/orders",
                                       {"location": LOCATION},
                                       HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
                resp = orders(req)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["location"], LOCATION)
        self.assertEqual(data["orders"], [])
        mock_list.assert_called_once()

    # --- POST ---

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_post_invalid_json(self):
        req = self.factory.post("/api/kiosk/orders",
                                data="not json",
                                content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = orders(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content)["error"], "invalid-json")

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_post_missing_location(self):
        req = self.factory.post("/api/kiosk/orders",
                                data=json.dumps({}),
                                content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = orders(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content)["error"], "missing-location")

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_post_no_auth(self):
        req = self.factory.post("/api/kiosk/orders",
                                data=json.dumps({"location": LOCATION}),
                                content_type="application/json")
        resp = orders(req)
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_post_success(self):
        fake_result = {
            "order_ref": "42",
            "barcode": "KIOSK:42999",
            "location": LOCATION,
            "transaction_id": 42,
            "created": True,
            "created_at": "2026-06-18T12:00:00",
            "expires_at": "2026-06-18T12:15:00",
            "soft_only": False,
            "status": "accepted",
            "total": "5.00",
            "lines": [],
            "slip": {},
        }
        with _mock_tillsession():
            with patch("emf.kiosk.place_order",
                       return_value=fake_result):
                with patch("emf.kiosk._auth_user", return_value=None):
                    req = self.factory.post(
                        "/api/kiosk/orders",
                        data=json.dumps({
                            "location": LOCATION,
                            "items": [{"stockline_id": 1, "qty": 1}],
                        }),
                        content_type="application/json",
                        HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
                    resp = orders(req)
        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.content)
        self.assertEqual(data["order_ref"], "42")


# ---------------------------------------------------------------------------
# cancel view
# ---------------------------------------------------------------------------

class CancelViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_unsupported_method(self):
        req = self.factory.post("/api/kiosk/orders/42")
        resp = order_detail(req, "42")
        self.assertEqual(resp.status_code, 405)

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_no_auth(self):
        req = self.factory.delete("/api/kiosk/orders/42")
        resp = order_detail(req, "42")
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN,
                       EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_bad_barcode(self):
        req = self.factory.delete(
            "/api/kiosk/orders/42",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            HTTP_ORDER_BARCODE="KIOSK:42000")
        resp = order_detail(req, "42")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(json.loads(resp.content)["error"], "bad-barcode")

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN,
                       EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_ref_barcode_mismatch(self):
        # Valid barcode for order 42, but the URL names a different order.
        req = self.factory.delete(
            "/api/kiosk/orders/99",
            HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
            HTTP_ORDER_BARCODE=_order_barcode(42))
        resp = order_detail(req, "99")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(json.loads(resp.content)["error"], "bad-barcode")

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN,
                       EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_order_not_found(self):
        barcode = _order_barcode(9999)
        mock_s = MagicMock()
        mock_s.query.return_value.filter.return_value.options.return_value \
            .with_for_update.return_value.one_or_none.return_value = None
        with _mock_tillsession(mock_s):
            req = self.factory.delete(
                "/api/kiosk/orders/9999",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
                HTTP_ORDER_BARCODE=barcode)
            resp = order_detail(req, "9999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(json.loads(resp.content)["error"], "not-found")

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN,
                       EMF_KIOSK_BARCODE_SECRET=SECRET,
                       EMF_KIOSK_CANCEL_MODE="delete")
    def test_delete_mode_deletes_record(self):
        trans = MagicMock(id=42, closed=False, payments=[], user=None)
        mock_s = MagicMock()
        mock_s.query.return_value.filter.return_value.options.return_value \
            .with_for_update.return_value.one_or_none.return_value = trans
        with _mock_tillsession(mock_s), \
                patch("emf.kiosk._read_meta",
                      return_value={"order_ref": "42",
                                    "location": LOCATION}), \
                patch("emf.kiosk._fallback_log_user",
                      return_value=None):
            req = self.factory.delete(
                "/api/kiosk/orders/42",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
                HTTP_ORDER_BARCODE=_order_barcode(42))
            resp = order_detail(req, "42")
        self.assertEqual(resp.status_code, 200)
        mock_s.delete.assert_called_once_with(trans)

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN,
                       EMF_KIOSK_BARCODE_SECRET=SECRET,
                       EMF_KIOSK_CANCEL_MODE="void")
    def test_void_mode_voids_and_keeps_record(self):
        line = MagicMock()
        trans = MagicMock(id=42, closed=False, payments=[], user=None,
                          lines=[line])
        mock_s = MagicMock()
        mock_s.query.return_value.filter.return_value.options.return_value \
            .with_for_update.return_value.one_or_none.return_value = trans
        with _mock_tillsession(mock_s), \
                patch("emf.kiosk._read_meta",
                      return_value={"order_ref": "42",
                                    "location": LOCATION}), \
                patch("emf.kiosk._fallback_log_user",
                      return_value=None):
            req = self.factory.delete(
                "/api/kiosk/orders/42",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}",
                HTTP_ORDER_BARCODE=_order_barcode(42))
            resp = order_detail(req, "42")
        self.assertEqual(resp.status_code, 200)
        line.void.assert_called_once()       # the line was voided
        mock_s.delete.assert_not_called()    # the record was NOT deleted
        self.assertTrue(trans.closed)        # transaction closed after voiding


# ---------------------------------------------------------------------------
# retrieve one: GET /api/kiosk/orders/<ref>
# ---------------------------------------------------------------------------

class OrderDetailGetTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_no_auth(self):
        req = self.factory.get("/api/kiosk/orders/42")
        resp = order_detail(req, "42")
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKEN=TOKEN)
    def test_not_found(self):
        mock_s = MagicMock()
        mock_s.query.return_value.filter.return_value.options.return_value \
            .one_or_none.return_value = None
        with _mock_tillsession(mock_s):
            req = self.factory.get(
                "/api/kiosk/orders/9999",
                HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
            resp = order_detail(req, "9999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(json.loads(resp.content)["error"], "not-found")


# ---------------------------------------------------------------------------
# mark_collected / mark_rejected
# ---------------------------------------------------------------------------

class MarkCollectedRejectedTests(TestCase):
    def test_collected_missing_order_raises_not_found(self):
        mock_s = MagicMock()
        mock_s.get.return_value = None
        with self.assertRaises(OrderNotFound) as cm:
            mark_collected(mock_s, order_ref="42", location=LOCATION)
        self.assertEqual(cm.exception.status_code, 404)

    def test_collected_wrong_location_raises_not_found(self):
        mock_s = MagicMock()
        mock_s.get.return_value = MagicMock()
        with patch("emf.kiosk._read_meta",
                   return_value={"location": "OtherBar"}):
            with self.assertRaises(OrderNotFound):
                mark_collected(mock_s, order_ref="42", location=LOCATION)

    def test_rejected_missing_order_raises_not_found(self):
        mock_s = MagicMock()
        mock_s.get.return_value = None
        with self.assertRaises(OrderNotFound):
            mark_rejected(mock_s, order_ref="42", location=LOCATION)

    def test_rejected_wrong_location_raises_not_found(self):
        mock_s = MagicMock()
        mock_s.get.return_value = MagicMock()
        with patch("emf.kiosk._read_meta",
                   return_value={"location": "OtherBar"}):
            with self.assertRaises(OrderNotFound):
                mark_rejected(mock_s, order_ref="42", location=LOCATION)
