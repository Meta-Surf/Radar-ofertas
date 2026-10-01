import unittest
from unittest.mock import Mock

from amazon_afiliados import AmazonCreators, valid_affiliate_url
from shopee_afiliados import AffiliateError
from ofertas_core import caption


ASIN = "B012345678"
TAG = "minhatag-20"
DETAIL = f"https://www.amazon.com.br/dp/{ASIN}?tag={TAG}&linkCode=ogi"


def response(status, data):
    r = Mock(status_code=status)
    r.json.return_value = data
    return r


def item(price=199.90, stock="IN_STOCK", detail=DETAIL):
    return {
        "asin": ASIN,
        "detailPageURL": detail,
        "images": {
            "primary": {
                "medium": {
                    "url": "https://m.media-amazon.com/images/I/test.jpg"
                }
            }
        },
        "itemInfo": {"title": {"displayValue": "Produto Amazon"}},
        "offersV2": {
            "listings": [{
                "isBuyBoxWinner": True,
                "availability": {"type": stock},
                "condition": {"value": "New"},
                "price": {"money": {"amount": price, "currency": "BRL"}},
            }]
        },
    }


class AmazonCreatorsTests(unittest.TestCase):
    def client(self, item_data=None):
        transport = Mock()
        transport.post.side_effect = [
            response(200, {
                "access_token": "token-seguro",
                "expires_in": 3600,
                "token_type": "bearer",
            }),
            response(200, {"itemsResult": {"items": [item_data or item()]}}),
        ]
        client = AmazonCreators(TAG, "credential", "secret", transport=transport)
        return client, transport
    def test_get_item_uses_oauth_and_returns_exact_brl_price(self):
        client, transport = self.client()
        result = client.request_item(ASIN)
        self.assertEqual(result["price"], "199,90")
        self.assertTrue(result["stock_confirmed"])
        self.assertEqual(result["affiliate_url"], DETAIL)
        token_call, api_call = transport.post.call_args_list
        self.assertEqual(token_call.kwargs["json"]["scope"], "creatorsapi::default")
        self.assertEqual(api_call.kwargs["headers"]["x-marketplace"], "www.amazon.com.br")
        self.assertEqual(api_call.kwargs["json"]["partnerTag"], TAG)
        self.assertIn("offersV2.listings.price", api_call.kwargs["json"]["resources"])

    def test_prepare_normalizes_product_and_preserves_api_price(self):
        client, _ = self.client()
        offer = {
            "product_id": "Amazon:" + ASIN,
            "store": "Amazon",
            "url": f"https://www.amazon.com.br/dp/{ASIN}",
            "source": "telegram",
            "price": "299,90",
        }
        ready = client.prepare(offer)
        self.assertEqual(ready["price"], "199,90")
        self.assertEqual(ready["url"], f"https://www.amazon.com.br/dp/{ASIN}")
        self.assertTrue(ready["affiliate_generated"])

    def test_out_of_stock_is_blocked(self):
        client, _ = self.client(item(stock="OUT_OF_STOCK"))
        with self.assertRaisesRegex(AffiliateError, "estoque"):
            client.request_item(ASIN)

    def test_wrong_partner_tag_is_blocked(self):
        client, _ = self.client(item(detail=f"https://www.amazon.com.br/dp/{ASIN}?tag=outro-20"))
        with self.assertRaisesRegex(AffiliateError, "afiliado"):
            client.request_item(ASIN)

    def test_invalid_price_is_blocked(self):
        client, _ = self.client(item(price=0))
        with self.assertRaisesRegex(AffiliateError, "preço"):
            client.request_item(ASIN)

    def test_valid_affiliate_url_requires_configured_tag(self):
        self.assertTrue(valid_affiliate_url(DETAIL, TAG))
        self.assertFalse(valid_affiliate_url(DETAIL, "outra-20"))

    def test_missing_credentials_fail_closed(self):
        with self.assertRaises(AffiliateError):
            AmazonCreators("", "", "")

    def test_caption_attributes_stock_to_amazon_not_awin(self):
        text = caption({
            "store": "Amazon",
            "name": "Produto",
            "price": "199,90",
            "stock_confirmed": True,
            "affiliate_generated": True,
        })
        self.assertIn("Amazon Creators API", text)
        self.assertNotIn("feed Awin", text)


if __name__ == "__main__":
    unittest.main()
