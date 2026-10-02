import hashlib
import importlib
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from shopee_afiliados import ShopeeAffiliate, AffiliateError
import publicacao_oferta as offer_publisher

URL='https://shopee.com.br/product/123/456'
LINK='https://s.shopee.com.br/MeuLinkTeste'

class AffiliateTests(unittest.TestCase):
    def client(self, data, status=200):
        transport=Mock()
        transport.post.return_value.status_code=status
        transport.post.return_value.json.return_value=data
        return ShopeeAffiliate('123456','SEGREDO_TESTE','telegram',transport),transport
    def test_signed_exact_bytes_and_no_foreign_tracking(self):
        client, transport=self.client({'data':{'generateShortLink':{'shortLink':LINK}}})
        with patch('shopee_afiliados.time.time',return_value=1234567890):
            self.assertEqual(client.generate_link(URL+'?utm_source=outro'),LINK)
        args=transport.post.call_args.kwargs
        body=args['data']
        self.assertIsInstance(body,bytes)
        expected=hashlib.sha256(b'1234561234567890'+body+b'SEGREDO_TESTE').hexdigest()
        self.assertIn('Signature='+expected,args['headers']['Authorization'])
        self.assertNotIn('outro',json.loads(body)['query'])
        self.assertFalse(args['allow_redirects'])
    def test_missing_credentials(self):
        with self.assertRaises(AffiliateError):
            ShopeeAffiliate('', '', transport=Mock())
    def test_graphql_error_does_not_expose_secret(self):
        c,_=self.client({'errors':[{'message':'invalid signature SEGREDO_TESTE'}]})
        with self.assertRaises(AffiliateError) as ctx:
            c.generate_link(URL)
        self.assertNotIn('SEGREDO_TESTE',str(ctx.exception))
    def test_empty_or_external_or_direct_link_blocked(self):
        for link in [None,'https://evil.test/a',URL]:
            c,_=self.client({'data':{'generateShortLink':{'shortLink':link}}})
            with self.assertRaises(AffiliateError):
                c.generate_link(URL)
    def test_http_rejected(self):
        c,_=self.client({},403)
        with self.assertRaises(AffiliateError):
            c.generate_link(URL)
    def test_metadata_failure_preserves_affiliate_link(self):
        c,_=self.client({})
        c.generate_link=Mock(return_value=LINK)
        c.details=Mock(side_effect=AffiliateError('falha'))
        result=c.prepare({'url':URL,'product_id':'Shopee:123:456'})
        self.assertEqual(result['affiliate_url'],LINK)
        self.assertTrue(result['affiliate_generated'])
    def test_wrong_product_metadata_ignored(self):
        c,_=self.client({'data':{'productOfferV2':{'nodes':[{'shopId':123,'itemId':999,'imageUrl':'https://x.susercontent.com/a.jpg'}]}}})
        self.assertEqual(c.details('Shopee:123:456'),{})
    def publisher(self):
        # Dependências substituídas só nos testes: nenhum envio ou autenticação.
        deps={'dotenv':Mock()}
        with patch.dict(sys.modules,deps):
            sys.modules.pop('bot_ofertas_revisao',None)
            return importlib.import_module('bot_ofertas_revisao')
    def test_send_button_has_returned_link(self):
        p=self.publisher()
        response=Mock()
        response.json.return_value={'ok':True,'result':{'message_id':12}}
        offer={'store':'Shopee','price':'99,90','affiliate_url':LINK,'affiliate_generated':True}
        with patch.object(offer_publisher.requests,'post',return_value=response) as post:
            p.send('FAKE_TOKEN','@teste',offer,None)
        data=post.call_args.kwargs['data']
        self.assertEqual(json.loads(data['reply_markup'])['inline_keyboard'][0][0]['url'],LINK)
        self.assertIn('(ANÚNCIO)',data['text'])
    def test_send_without_affiliate_never_calls_telegram(self):
        p=self.publisher()
        with patch.object(offer_publisher.requests,'post') as post:
            with self.assertRaises(AffiliateError):
                p.send('fake','@teste',{'url':URL},None)
            post.assert_not_called()
    def test_api_failure_releases_reservation_and_no_send(self):
        p=self.publisher()
        offer={'url':URL,'product_id':'Shopee:123:456','price':'99,90','source_date':datetime.now(timezone.utc).isoformat()}
        from ofertas_core import Ledger
        ledger=Ledger(':memory:')
        client=Mock(); client.prepare.side_effect=AffiliateError('API recusada')
        with patch.dict(os.environ,{'TELEGRAM_TOKEN':'fake','TELEGRAM_CANAL':'@teste'}), patch.object(sys,'argv',['bot.py']), patch.object(p,'rows',return_value=iter([offer])), patch.object(p,'Ledger',return_value=ledger), patch.object(p.ShopeeAffiliate,'from_env',return_value=client), patch.object(p,'send') as send, patch.object(p.time,'sleep',side_effect=KeyboardInterrupt), redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                p.main()
            send.assert_not_called()
            self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0], 0)
            ledger.db.close()

if __name__=='__main__':
    unittest.main()

