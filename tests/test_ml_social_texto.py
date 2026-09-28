import os
import unittest
from unittest.mock import Mock, patch

import cupons_mercadolivre as ml
from shopee_afiliados import AffiliateError
from tests.test_cupons_ml import build, FIRST


class SocialTextTests(unittest.TestCase):
    def setUp(self):
        p = patch.dict(os.environ, {'ML_CUPONS_SOCIAL_URL': ml.DEFAULT_SOCIAL_URL})
        p.start(); self.addCleanup(p.stop)

    def test_origin_ctas_and_links_replaced_by_one_social_footer(self):
        for line in ['', 'Resgate aqui', 'LINK:', '👉 Resgate aqui: https://meli.la/terceiro',
                     '[Resgate aqui](https://meli.la/terceiro)', 'https://meli.la/terceiro']:
            with self.subTest(line=line):
                text = ml.alert_caption(build(FIRST + '\n' + line))
                self.assertEqual(text.count(ml.DEFAULT_SOCIAL_URL), 1)
                self.assertEqual(text.count('Resgate aqui:'), 1)
                self.assertNotIn('LINK:', text)
                self.assertNotIn('meli.la', text)
                self.assertEqual(text.count('<code>'), 14)

    def test_send_never_creates_buttons_or_keeps_old_manual_links(self):
        response = Mock(); response.json.return_value = {'ok': True, 'result': {'message_id': 99}}
        entry = dict(build(), manual_links=['https://meli.la/antigo'])
        with patch('requests.post', return_value=response) as post:
            ml.send_alert('fake', '@fake', entry)
            post.assert_called_once()
            data = post.call_args.kwargs['data']
            self.assertNotIn('reply_markup', data)
            self.assertTrue(data['text'].endswith(ml.DEFAULT_SOCIAL_URL))
            self.assertNotIn('antigo', data['text'])

    def test_photo_length_includes_added_social_url(self):
        entry = build('MERCADO LIVRE\n10% OFF: CUPOMTESTE\n' + 'A' * 970)
        self.assertGreater(ml.visible_length(ml.alert_caption(entry)), 1024)
        response = Mock(); response.json.return_value = {'ok': True, 'result': {'message_id': 99}}
        with patch('requests.post', return_value=response) as post:
            ml.send_alert('fake', '@fake', entry, Mock())
            self.assertTrue(post.call_args.args[0].endswith('/sendMessage'))
            self.assertIn('A'*970, post.call_args.kwargs['data']['text'])

    def test_rendering_twice_does_not_duplicate_footer(self):
        entry = build()
        once = ml.alert_caption(entry)
        twice = ml.alert_caption(dict(entry, text=once))
        self.assertEqual(once, twice)

    def test_social_configuration_validated_before_send(self):
        for value in ['http://www.mercadolivre.com.br/social/bebidastaio',
                      'https://www.mercadolivre.com.br.evil.test/social/bebidastaio',
                      'https://meli.la/outro', 'https://www.mercadolivre.com.br/p/MLB123']:
            with self.subTest(value=value), patch.dict(os.environ, {'ML_CUPONS_SOCIAL_URL': value}), patch('requests.post') as post:
                with self.assertRaises(AffiliateError):
                    ml.send_alert('fake', '@fake', build())
                post.assert_not_called()
