import os
import unittest
from unittest.mock import patch

from mercadolivre_afiliados import (
    ENDPOINT,
    LINKBUILDER,
    MercadoLivreAffiliate,
    MercadoLivreSessionError,
    valid_affiliate_url,
)
from shopee_afiliados import AffiliateError


URL = "https://produto.mercadolivre.com.br/MLB-4360643061-_JM"
CANONICAL = "https://produto.mercadolivre.com.br/MLB-4360643061-_JM"
SHORT = "https://meli.la/AbCd123"


class Response:
    def __init__(self, status=200, data=None, cookies=None):
        self.status_code = status
        self._data = data if data is not None else {}
        self.cookies = cookies or {}

    def json(self):
        return self._data


class Transport:
    def __init__(self, get_response=None, post_response=None):
        self.get_response = get_response or Response()
        self.post_response = post_response or Response(
            data={"urls": [{"short_url": SHORT}]}
        )
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return self.get_response

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_response


class MercadoLivreAffiliateTests(unittest.TestCase):
    def client(self, transport=None, refresh=True):
        return MercadoLivreAffiliate(
            "_csrf=abc123456789; ssid=session-value-123456789",
            "csrf-token-123456",
            "minhatag",
            transport=transport or Transport(),
            refresh_cookies=refresh,
        )

    def test_generates_from_canonical_product_and_sends_required_context(self):
        transport = Transport(get_response=Response(cookies={"ssid": "renewed"}))
        client = self.client(transport)
        self.assertEqual(client.generate_link(URL), SHORT)
        self.assertEqual(transport.get_calls[0][0], LINKBUILDER)
        endpoint, call = transport.post_calls[0]
        self.assertEqual(endpoint, ENDPOINT)
        self.assertEqual(call["json"], {"urls": [CANONICAL], "tag": "minhatag"})
        self.assertEqual(call["headers"]["X-CSRF-Token"], "csrf-token-123456")
        self.assertIn("ssid=renewed", call["headers"]["Cookie"])
        self.assertEqual(call["headers"]["Origin"], "https://www.mercadolivre.com.br")
        self.assertEqual(call["headers"]["Referer"], LINKBUILDER)

    def test_destination_requires_and_uses_own_tag(self):
        transport = Transport()
        client = self.client(transport, refresh=False)
        with self.assertRaises(AffiliateError):
            client.generate_link(URL, destination='instagram')
        with patch.dict(os.environ, {'ML_AFFILIATE_TAG_INSTAGRAM':'insta_tag'}):
            self.assertEqual(client.generate_link(URL, destination='instagram'), SHORT)
        self.assertEqual(transport.post_calls[-1][1]['json']['tag'], 'insta_tag')

    def test_cache_avoids_generating_same_link_twice(self):
        transport = Transport()
        client = self.client(transport, refresh=False)
        self.assertEqual(client.generate_link(URL), SHORT)
        self.assertEqual(client.generate_link(URL), SHORT)
        self.assertEqual(len(transport.post_calls), 1)

    def test_prepare_marks_automatic_ml_offer(self):
        client = self.client(Transport(), refresh=False)
        offer = {
            "kind": "ml_offer",
            "source": "telegram",
            "store": "Mercado Livre",
            "product_id": "MercadoLivre:4360643061",
            "url": URL,
            "price": "2.943,00",
        }
        prepared = client.prepare(offer)
        self.assertTrue(prepared["affiliate_generated"])
        self.assertEqual(prepared["affiliate_url"], SHORT)

    def test_session_rejection_is_fail_closed(self):
        client = self.client(
            Transport(post_response=Response(status=403)), refresh=False
        )
        with self.assertRaisesRegex(MercadoLivreSessionError, "Sessão de afiliado") as ctx:
            client.generate_link(URL)
        self.assertEqual(ctx.exception.status, 403)

    def test_rejects_invalid_returned_destination(self):
        client = self.client(
            Transport(
                post_response=Response(
                    data={"urls": [{"short_url": "https://evil.example/link"}]}
                )
            ),
            refresh=False,
        )
        with self.assertRaisesRegex(AffiliateError, "link de afiliado válido"):
            client.generate_link(URL)

    def test_config_and_affiliate_url_validation(self):
        self.assertTrue(valid_affiliate_url(SHORT))
        self.assertFalse(valid_affiliate_url("http://meli.la/abc"))
        self.assertFalse(valid_affiliate_url("https://meli.la.evil.example/abc"))
        with self.assertRaises(AffiliateError):
            MercadoLivreAffiliate("", "csrf-token-123", "tag")


if __name__ == "__main__":
    unittest.main()
