import unittest
from types import SimpleNamespace

import canal_espelho as mirror
from shopee_afiliados import AffiliateError
import bot_ofertas_revisao as publisher
import monitor_ofertas as monitor
from unittest.mock import Mock, patch


def message(text, *, buttons=None):
    rows = []
    if buttons:
        rows = [SimpleNamespace(buttons=[
            SimpleNamespace(text=label, url=url) for label, url in buttons
        ])]
    return SimpleNamespace(
        raw_text=text,
        entities=[],
        get_entities_text=lambda: [],
        reply_markup=SimpleNamespace(rows=rows) if rows else None,
    )


class CanalEspelhoTests(unittest.TestCase):
    def test_preserva_texto_remove_social_e_troca_apenas_link_da_loja(self):
        source = "https://meli.la/2EEaaBT"
        social = "https://instagram.com/canal"
        msg = message(
            "🔥🙏 Suporte Cockpit Para Volante Logitech G27 G29 Simulador. Preto\n\n"
            "R$ 399,00\n"
            "✅ R$ 345,02 À vista\n\n"
            "💳 Em até 9x de R$ 42,60 sem juros com cartão Mercado Pago\n\n"
            f"👉 Link: {source}\n\n"
            f"Instagram: {social}\n\n"
            "🚨 Oferta e/ou cupom por tempo limitado, aproveite! 🙏\n\n"
            "(anúncio)"
        )
        template, button = mirror.build_template([msg], [source])
        self.assertIsNone(button)
        self.assertNotIn("instagram", template.lower())
        self.assertNotIn(social, template)
        self.assertNotIn("Saiba mais", template)
        self.assertNotIn("exemplo.com", template)
        self.assertIn("R$ 399,00", template)
        self.assertIn("R$ 345,02", template)
        self.assertIn("(anúncio)", template)

        affiliate = "https://meli.la/MINHAOFERTA"
        rendered = mirror.render(template, affiliate)
        self.assertIn(affiliate, rendered)
        self.assertNotIn(source, rendered)
        self.assertIn("👉 Link:", rendered)

    def test_botao_de_loja_pode_ser_preservado_sem_copiar_outros_botoes(self):
        source = "https://s.shopee.com.br/abc123"
        msg = message("🔥 Oferta especial\nR$ 99,90", buttons=[
            ("Comprar", source),
            ("Instagram", "https://instagram.com/canal"),
        ])
        template, button = mirror.build_template([msg], [source])
        self.assertEqual(button, "Comprar")
        self.assertNotIn("instagram", template.lower())
        self.assertEqual(mirror.render(template, "https://s.shopee.com.br/meulink"), template)

    def test_perfil_social_e_sites_externos_nao_sao_lojas_de_produto(self):
        self.assertFalse(mirror.supported_store_url("https://www.mercadolivre.com.br/social/outroafiliado"))
        self.assertFalse(mirror.supported_store_url("https://instagram.com/canal"))
        self.assertFalse(mirror.supported_store_url("https://desconto.games/oferta"))
        self.assertTrue(mirror.supported_store_url("https://meli.la/2EEaaBT"))
        self.assertTrue(mirror.supported_store_url("https://s.shopee.com.br/abc123"))

    def test_render_bloqueia_link_externo_residual(self):
        with self.assertRaises(AffiliateError):
            mirror.render(
                mirror.PLACEHOLDER + "\nhttps://instagram.com/canal",
                "https://meli.la/MINHAOFERTA",
            )


    def test_publicador_espelho_nao_reescreve_nem_exige_preco_parseado(self):
        affiliate = "https://s.shopee.com.br/meulink"
        offer = {
            "kind": "product_offer",
            "store": "Shopee",
            "publish_mode": "mirror",
            "affiliate_generated": True,
            "affiliate_url": affiliate,
            "mirror_template": "🔥 Oferta muito bem formatada\n\n👉 Link: " + mirror.PLACEHOLDER,
            "mirror_button_text": None,
        }
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 321}}
        with patch.object(publisher.requests, "post", return_value=response) as post:
            message_id, wait = publisher.send("token", "@destino", offer, None)
        self.assertEqual((message_id, wait), (321, 0))
        data = post.call_args.kwargs["data"]
        self.assertIn(affiliate, data["text"])
        self.assertNotIn(mirror.PLACEHOLDER, data["text"])
        self.assertNotIn("reply_markup", data)


    def test_produto_com_codigo_de_cupom_nao_vira_alerta_separado(self):
        source = "https://shopee.com.br/product/123/456"
        msg = message(
            "🔥 Air Fryer em oferta\n"
            "✅ R$ 233,00 à vista com cupom: MELIDATADUPLA\n"
            "👉 Link: " + source
        )
        fake_alerts = [{"kind": "coupon_alert", "entries": [{"url": source}]}]
        self.assertFalse(
            monitor.exclusive_coupon_message(msg.raw_text, [msg], fake_alerts, None)
        )

    def test_mensagem_exclusiva_de_cupons_mantem_fluxo_da_arte(self):
        msg = message(
            "🔥 CUPONS SHOPEE\n"
            "🏷️ 10% OFF em selecionados\n"
            "👉 Resgate aqui: https://s.shopee.com.br/cupom123"
        )
        fake_alerts = [{"kind": "coupon_alert", "entries": [{"url": "https://s.shopee.com.br/cupom123"}]}]
        self.assertTrue(
            monitor.exclusive_coupon_message(msg.raw_text, [msg], fake_alerts, None)
        )

    def test_link_direto_de_produto_sem_preco_tambem_nao_vira_alerta(self):
        source = "https://shopee.com.br/product/123/456"
        msg = message("🔥 Produto com cupom: TESTE10\n👉 Link: " + source)
        fake_alerts = [{"kind": "coupon_alert", "entries": [{"url": source}]}]
        self.assertFalse(
            monitor.exclusive_coupon_message(msg.raw_text, [msg], fake_alerts, None)
        )


    def test_shopee_vip_e_descartado_e_produto_permanece(self):
        product_url = "https://s.shopee.com.br/1BMiv3rxzA"
        vip_url = "https://s.shopee.com.br/5foq7KHI00"
        msg = message(
            "🔥🙏 Placa de Vídeo MSI GeForce RTX 5050 GAMING OC 8G 912-V538-009\n\n"
            "✅ R$ 2.023,00 À vista (consulte como fica parcelado)\n\n"
            f"👉 Link: {product_url}\n\n"
            "🚨 Oferta e/ou cupom por tempo limitado, aproveite! 🙏\n\n"
            f"🎁 Assine o Shopee VIP (Teste grátis) e tenha frete grátis e cupons exclusivos: {vip_url}\n\n"
            "(anúncio)"
        )
        product = ("Shopee:344381236:45465789471", "Shopee",
                   "https://shopee.com.br/product/344381236/45465789471")
        with patch.object(
            monitor, "resolve_for_capture",
            side_effect=[(product, ""), (None, "Página sem produto identificável.")]
        ):
            candidates, ignored = __import__("asyncio").run(
                monitor.mirror_product_candidates([msg], set())
            )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_url"], product_url)
        self.assertEqual(len(ignored), 1)
        self.assertEqual(ignored[0][0], vip_url)

        template, _ = mirror.build_template([msg], [product_url])
        self.assertIn("Placa de Vídeo MSI GeForce RTX 5050", template)
        self.assertIn("R$ 2.023,00", template)
        self.assertIn(mirror.PLACEHOLDER, template)
        self.assertNotIn("Shopee VIP", template)
        self.assertNotIn(vip_url, template)


if __name__ == "__main__":
    unittest.main()
