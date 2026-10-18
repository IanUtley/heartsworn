"""Characterization tests for the purchase routing in
``HCPHandler._handle_service_request_legacy``.  Purchases must never fall
through to the legacy debit-and-grant path, so these pin the registry, the
transactional handler, and the failure responses."""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.test_db import fresh_database

SRC = fresh_database()

import hconnect_server as hcs
from services import store

PURCHASE = 6011


def _call(handler, data_type):
    return hcs.HCPHandler._handle_service_request_legacy(
        handler, 1, 2, data_type, 3, False, "session", "conh", {"Id": 9}, b"")


def _handler():
    handler = object.__new__(hcs.HCPHandler)
    handler.user_profile = None
    return handler


def test_registry_hit_short_circuits():
    handler = _handler()
    with mock.patch.object(hcs, "_dispatch_service", return_value=True) as dispatch, \
            mock.patch.object(store, "handle_purchase") as purchase:
        _call(handler, PURCHASE)
    dispatch.assert_called_once()
    purchase.assert_not_called()


def test_registry_error_on_purchase_sends_failure_and_stops():
    handler = _handler()
    with mock.patch.object(hcs, "_dispatch_service", side_effect=RuntimeError("boom")), \
            mock.patch.object(store, "send_purchase_failure") as failure, \
            mock.patch.object(store, "handle_purchase") as purchase:
        _call(handler, PURCHASE)
    failure.assert_called_once()
    assert failure.call_args.args[-1] == "Purchase could not be completed."
    purchase.assert_not_called()


def test_registry_miss_uses_transactional_purchase():
    handler = _handler()
    with mock.patch.object(hcs, "_dispatch_service", return_value=False), \
            mock.patch.object(store, "handle_purchase") as purchase, \
            mock.patch.object(store, "send_purchase_failure") as failure:
        _call(handler, PURCHASE)
    purchase.assert_called_once()
    failure.assert_not_called()


def test_purchase_handler_crash_sends_failure():
    handler = _handler()
    with mock.patch.object(hcs, "_dispatch_service", return_value=False), \
            mock.patch.object(store, "handle_purchase", side_effect=RuntimeError("db")), \
            mock.patch.object(store, "send_purchase_failure") as failure:
        _call(handler, PURCHASE)
    failure.assert_called_once()
    assert failure.call_args.args[-1] == "Purchase could not be completed."


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("PASS", test.__name__)
    print("Service request routing tests passed")
