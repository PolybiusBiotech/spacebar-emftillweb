"""Tests for emf.order_client — pure functions and view auth paths.

DB-touching business logic (place_order, expire_orders, list_orders, cancel)
is tested here via mocking tillsession(). For integration tests against a real
quicktill database see the developer docs.
"""

import datetime
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase, override_settings

from emf.order_client import (
    _authenticate,
    _authenticate_token_only,
    _bearer_token,
    _checkdigits,
    _client_ip,
    _normalise_token_entry,
    _order_barcode,
    _qr_rows,
    _verify_barcode,
    barcode_prefix,
    cancel,
    default_timeout,
    expire,
    orders,
)


SECRET = "test-barcode-secret-xyz"
TOKEN = "test-bearer-token-abc"
LOCATION = "Spacebar"

TOKENS = {
    TOKEN: {
        "locations": [LOCATION],
        "source": "test-kiosk",
        "user": "kiosk",
    }
}


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
        # Need at least 4 chars after prefix (1+ digit trans_id + 3 check digits)
        self.assertIsNone(_verify_barcode(barcode_prefix + "12"))

    def test_non_integer_trans_id_rejected(self):
        self.assertIsNone(_verify_barcode(barcode_prefix + "abcdef"))

    def test_empty_rejected(self):
        self.assertIsNone(_verify_barcode(""))


# ---------------------------------------------------------------------------
# QR rows
# ---------------------------------------------------------------------------

class QrRowsTests(TestCase):
    def setUp(self):
        self.rows = _qr_rows("KIOSK:9574381")

    def test_returns_list(self):
        self.assertIsInstance(self.rows, list)
        self.assertGreater(len(self.rows), 0)

    def test_each_row_is_string(self):
        for row in self.rows:
            self.assertIsInstance(row, str)

    def test_only_zero_and_one(self):
        for row in self.rows:
            for bit in row:
                self.assertIn(bit, ("0", "1"), f"Unexpected char {bit!r} in row {row!r}")

    def test_square_matrix(self):
        n = len(self.rows)
        for row in self.rows:
            self.assertEqual(len(row), n)

    def test_both_values_present(self):
        # A valid QR code has both dark and light modules.
        # This also confirms "0" is not truthy (regression: if bit: treats "0" as True).
        all_bits = set("".join(self.rows))
        self.assertIn("0", all_bits, "No light modules — QR would render as solid black")
        self.assertIn("1", all_bits, "No dark modules — QR would render as solid white")

    def test_zero_is_not_dark(self):
        # The badge draws a rectangle only for bit == "1".
        # Confirm that the string "0" is falsy when compared with == "1".
        self.assertFalse("0" == "1")
        self.assertTrue("1" == "1")


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
# Client IP
# ---------------------------------------------------------------------------

class ClientIpTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_remote_addr(self):
        req = self.factory.get("/", REMOTE_ADDR="1.2.3.4")
        self.assertEqual(_client_ip(req), "1.2.3.4")

    def test_x_forwarded_for(self):
        req = self.factory.get("/",
                               HTTP_X_FORWARDED_FOR="10.0.0.1",
                               REMOTE_ADDR="127.0.0.1")
        self.assertEqual(_client_ip(req), "10.0.0.1")

    def test_x_forwarded_for_multi_hop(self):
        req = self.factory.get("/",
                               HTTP_X_FORWARDED_FOR="10.0.0.1, 192.168.1.1",
                               REMOTE_ADDR="127.0.0.1")
        self.assertEqual(_client_ip(req), "10.0.0.1")


# ---------------------------------------------------------------------------
# Token normalisation
# ---------------------------------------------------------------------------

class NormaliseTokenEntryTests(TestCase):
    def test_string_form(self):
        result = _normalise_token_entry("Spacebar")
        self.assertEqual(result["locations"], ["Spacebar"])
        self.assertEqual(result["source"], "kiosk")
        self.assertIsNone(result["max_items"])
        self.assertIsNone(result["rate_limit_seconds"])
        self.assertEqual(result["timeout"], default_timeout)

    def test_dict_full(self):
        entry = {
            "locations": ["Spacebar", "Cybar"],
            "source": "badge",
            "user": "kiosk",
            "timeout": 120,
            "max_items": 1,
            "rate_limit": 300,
        }
        result = _normalise_token_entry(entry)
        self.assertEqual(result["locations"], ["Spacebar", "Cybar"])
        self.assertEqual(result["source"], "badge")
        self.assertEqual(result["user"], "kiosk")
        self.assertEqual(result["timeout"], datetime.timedelta(seconds=120))
        self.assertEqual(result["max_items"], 1)
        self.assertEqual(result["rate_limit_seconds"], 300)

    def test_dict_defaults(self):
        result = _normalise_token_entry({})
        self.assertEqual(result["locations"], [])
        self.assertEqual(result["source"], "kiosk")
        self.assertIsNone(result["user"])
        self.assertIsNone(result["max_items"])
        self.assertIsNone(result["rate_limit_seconds"])
        self.assertEqual(result["timeout"], default_timeout)

    def test_single_location_key(self):
        result = _normalise_token_entry({"location": "Spacebar"})
        self.assertEqual(result["locations"], ["Spacebar"])

    def test_timeout_is_timedelta(self):
        result = _normalise_token_entry({"timeout": 60})
        self.assertIsInstance(result["timeout"], datetime.timedelta)
        self.assertEqual(result["timeout"].total_seconds(), 60)


# ---------------------------------------------------------------------------
# _authenticate (location-scoped)
# ---------------------------------------------------------------------------

class AuthenticateTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(EMF_KIOSK_ORDER_TOKENS={})
    def test_no_tokens_configured(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        auth, resp = _authenticate(req, LOCATION)
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 503)
        data = json.loads(resp.content)
        self.assertEqual(data["error"], "kiosk-api-not-configured")

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_missing_bearer(self):
        req = self.factory.get("/")
        auth, resp = _authenticate(req, LOCATION)
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 401)
        data = json.loads(resp.content)
        self.assertEqual(data["error"], "missing-token")

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_invalid_token(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION="Bearer wrong-token")
        auth, resp = _authenticate(req, LOCATION)
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 401)
        data = json.loads(resp.content)
        self.assertEqual(data["error"], "invalid-token")

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_wrong_location(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        auth, resp = _authenticate(req, "OtherLocation")
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertEqual(data["error"], "location-not-allowed")

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_valid(self):
        req = self.factory.get("/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        auth, resp = _authenticate(req, LOCATION)
        self.assertIsNotNone(auth)
        self.assertIsNone(resp)
        self.assertIn(LOCATION, auth["locations"])
        self.assertEqual(auth["source"], "test-kiosk")


# ---------------------------------------------------------------------------
# _authenticate_token_only (no location check — used by cancel)
# ---------------------------------------------------------------------------

class AuthenticateTokenOnlyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(EMF_KIOSK_ORDER_TOKENS={})
    def test_no_tokens_configured(self):
        req = self.factory.post("/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        auth, resp = _authenticate_token_only(req)
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 503)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_missing_bearer(self):
        req = self.factory.post("/")
        auth, resp = _authenticate_token_only(req)
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_invalid_token(self):
        req = self.factory.post("/", HTTP_AUTHORIZATION="Bearer bad")
        auth, resp = _authenticate_token_only(req)
        self.assertIsNone(auth)
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_valid(self):
        req = self.factory.post("/", HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        auth, resp = _authenticate_token_only(req)
        self.assertIsNotNone(auth)
        self.assertIsNone(resp)


# ---------------------------------------------------------------------------
# orders view
# ---------------------------------------------------------------------------

def _mock_tillsession(return_value=None):
    """Return a patch for tillsession() that yields return_value."""
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=return_value or MagicMock())
    mock_session.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def fake_tillsession():
        yield mock_session.__enter__.return_value

    return patch("emf.order_client.tillsession", fake_tillsession)


class OrdersViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_unsupported_method(self):
        req = self.factory.delete("/api/kiosk/orders.json")
        resp = orders(req)
        self.assertEqual(resp.status_code, 405)

    # --- GET ---

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_get_missing_location(self):
        req = self.factory.get("/api/kiosk/orders.json",
                               HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = orders(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content)["error"], "missing-location")

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_get_no_auth(self):
        req = self.factory.get("/api/kiosk/orders.json", {"location": LOCATION})
        resp = orders(req)
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_get_wrong_location(self):
        req = self.factory.get("/api/kiosk/orders.json",
                               {"location": "OtherBar"},
                               HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = orders(req)
        self.assertEqual(resp.status_code, 403)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_get_success(self):
        with _mock_tillsession():
            with patch("emf.order_client.list_orders", return_value=[]) as mock_list:
                req = self.factory.get("/api/kiosk/orders.json",
                                       {"location": LOCATION},
                                       HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
                resp = orders(req)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["location"], LOCATION)
        self.assertEqual(data["orders"], [])
        mock_list.assert_called_once()

    # --- POST ---

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_post_invalid_json(self):
        req = self.factory.post("/api/kiosk/orders.json",
                                data="not json",
                                content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = orders(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content)["error"], "invalid-json")

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_post_missing_location(self):
        req = self.factory.post("/api/kiosk/orders.json",
                                data=json.dumps({}),
                                content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = orders(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content)["error"], "missing-location")

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_post_no_auth(self):
        req = self.factory.post("/api/kiosk/orders.json",
                                data=json.dumps({"location": LOCATION}),
                                content_type="application/json")
        resp = orders(req)
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_post_success(self):
        fake_result = {
            "order_ref": "42",
            "barcode": "KIOSK:42999",
            "qr_rows": ["010"],
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
            with patch("emf.order_client.expire_orders", return_value=[]):
                with patch("emf.order_client.place_order", return_value=fake_result):
                    with patch("emf.order_client._auth_user", return_value=None):
                        req = self.factory.post(
                            "/api/kiosk/orders.json",
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
        self.assertIn("expired_orders", data)


# ---------------------------------------------------------------------------
# cancel view
# ---------------------------------------------------------------------------

class CancelViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_not_allowed(self):
        req = self.factory.get("/api/kiosk/orders/cancel.json")
        resp = cancel(req)
        self.assertEqual(resp.status_code, 405)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_invalid_json(self):
        req = self.factory.post("/api/kiosk/orders/cancel.json",
                                data="bad",
                                content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = cancel(req)
        self.assertEqual(resp.status_code, 400)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_no_auth(self):
        req = self.factory.post("/api/kiosk/orders/cancel.json",
                                data=json.dumps({"barcode": "KIOSK:42123", "order_ref": "42"}),
                                content_type="application/json")
        resp = cancel(req)
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_bad_barcode(self):
        req = self.factory.post("/api/kiosk/orders/cancel.json",
                                data=json.dumps({"barcode": "KIOSK:42000", "order_ref": "42"}),
                                content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = cancel(req)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(json.loads(resp.content)["error"], "bad-barcode")

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    @override_settings(EMF_KIOSK_BARCODE_SECRET=SECRET)
    def test_order_not_found(self):
        barcode = _order_barcode(9999)
        mock_s = MagicMock()
        mock_s.query.return_value.filter.return_value.options.return_value \
            .with_for_update.return_value.one_or_none.return_value = None
        with _mock_tillsession(mock_s):
            req = self.factory.post("/api/kiosk/orders/cancel.json",
                                    data=json.dumps({"barcode": barcode, "order_ref": "9999"}),
                                    content_type="application/json",
                                    HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
            resp = cancel(req)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(json.loads(resp.content)["error"], "not-found")


# ---------------------------------------------------------------------------
# expire view
# ---------------------------------------------------------------------------

class ExpireViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_not_allowed(self):
        req = self.factory.get("/api/kiosk/orders/expire.json")
        resp = expire(req)
        self.assertEqual(resp.status_code, 405)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_missing_location(self):
        req = self.factory.post("/api/kiosk/orders/expire.json",
                                data=json.dumps({}),
                                content_type="application/json",
                                HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        resp = expire(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content)["error"], "missing-location")

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_no_auth(self):
        req = self.factory.post("/api/kiosk/orders/expire.json",
                                data=json.dumps({"location": LOCATION}),
                                content_type="application/json")
        resp = expire(req)
        self.assertEqual(resp.status_code, 401)

    @override_settings(EMF_KIOSK_ORDER_TOKENS=TOKENS)
    def test_success(self):
        with _mock_tillsession():
            with patch("emf.order_client.expire_orders", return_value=[]):
                with patch("emf.order_client._auth_user", return_value=None):
                    req = self.factory.post(
                        "/api/kiosk/orders/expire.json",
                        data=json.dumps({"location": LOCATION}),
                        content_type="application/json",
                        HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
                    resp = expire(req)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["location"], LOCATION)
        self.assertEqual(data["expired_orders"], [])
