import unittest
from types import SimpleNamespace

import canal_espelho as mirror
from shopee_afiliados import AffiliateError
import bot_ofertas_revisao as publisher
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


if __name__ == "__main__":
    unittest.main()
