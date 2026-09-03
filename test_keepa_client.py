"""
Regression test for the UPC-mismatch bug: a product used to be able to get
attached to every UPC in a 100-code batch when Keepa's upcList/eanList
didn't reverse-match cleanly. Fetching one UPC per request (the fix) should
make that structurally impossible - each response is unambiguously scoped
to the one UPC that was queried.

Mocks the HTTP layer so this runs with no real Keepa API key or network
access. Run: python3 test_keepa_client.py
"""
from unittest.mock import patch, MagicMock
import keepa_client


def _fake_response(status_code, products=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = {"products": products or []}
    return resp


def test_each_upc_gets_only_its_own_product():
    """The core regression check: UPC A's product must never leak onto UPC B."""
    product_a = {"asin": "B0AAAAAAA1", "title": "Product A", "csv": [], "stats": {}}
    product_b = {"asin": "B0BBBBBBB2", "title": "Product B", "csv": [], "stats": {}}

    def fake_get(url, params=None, timeout=None):
        upc = params["code"]
        if upc == "111111111111":
            return _fake_response(200, [product_a])
        elif upc == "222222222222":
            return _fake_response(200, [product_b])
        else:
            return _fake_response(200, [])  # no match, e.g. "333333333333"

    with patch("requests.Session.get", side_effect=fake_get):
        results, errors = keepa_client.fetch_products_by_upc(
            "fake-key", ["111111111111", "222222222222", "333333333333"]
        )

    assert errors == {}, f"Expected no errors, got {errors}"
    assert len(results["111111111111"]) == 1
    assert results["111111111111"][0]["asin"] == "B0AAAAAAA1"
    assert len(results["222222222222"]) == 1
    assert results["222222222222"][0]["asin"] == "B0BBBBBBB2"
    assert results["333333333333"] == [], "UPC with no match should be an empty list, not borrow another product"
    print("PASS: test_each_upc_gets_only_its_own_product")


def test_no_match_stays_no_match_even_at_scale():
    """Simulates a batch where most UPCs have zero matches - none of them
    should end up borrowing a neighboring UPC's product (the old bug)."""
    real_product = {"asin": "B0REALREAL", "title": "The Real Product", "csv": [], "stats": {}}

    def fake_get(url, params=None, timeout=None):
        upc = params["code"]
        if upc == "999999999999":
            return _fake_response(200, [real_product])
        return _fake_response(200, [])

    upcs = [str(100000000000 + i) for i in range(50)] + ["999999999999"]
    with patch("requests.Session.get", side_effect=fake_get):
        results, errors = keepa_client.fetch_products_by_upc("fake-key", upcs)

    no_match_upcs = [u for u in upcs if u != "999999999999"]
    for u in no_match_upcs:
        assert results[u] == [], f"UPC {u} should have no match, but got {results[u]}"
    assert results["999999999999"][0]["asin"] == "B0REALREAL"
    print("PASS: test_no_match_stays_no_match_even_at_scale")


def test_auth_error_aborts_cleanly():
    def fake_get(url, params=None, timeout=None):
        return _fake_response(401, text="invalid key")

    with patch("requests.Session.get", side_effect=fake_get):
        try:
            keepa_client.fetch_products_by_upc("bad-key", ["111111111111", "222222222222"])
            assert False, "Expected KeepaError to be raised"
        except keepa_client.KeepaError as e:
            assert "auth error" in str(e)
    print("PASS: test_auth_error_aborts_cleanly")


def test_transient_error_is_reported_not_silently_dropped():
    def fake_get(url, params=None, timeout=None):
        upc = params["code"]
        if upc == "444444444444":
            return _fake_response(500, text="server error")
        return _fake_response(200, [])

    with patch("requests.Session.get", side_effect=fake_get), patch("time.sleep"):
        results, errors = keepa_client.fetch_products_by_upc(
            "fake-key", ["111111111111", "444444444444"]
        )

    assert "444444444444" in errors, "A persistently-failing UPC should show up in errors, not vanish silently"
    assert results["444444444444"] == []
    print("PASS: test_transient_error_is_reported_not_silently_dropped")


if __name__ == "__main__":
    test_each_upc_gets_only_its_own_product()
    test_no_match_stays_no_match_even_at_scale()
    test_auth_error_aborts_cleanly()
    test_transient_error_is_reported_not_silently_dropped()
    print("\nAll keepa_client regression tests passed.")
