import os
import unittest
from unittest.mock import patch

from configuracao import (
    DistributionConfig,
    GateConfig,
    MonitorConfig,
    PublisherConfig,
    TelegramConfig,
    env_bool,
    env_csv,
    env_int,
    operational_snapshot,
    validate_operational_config,
)


class ConfiguracaoTests(unittest.TestCase):
    def test_defaults_operacionais_sao_unicos_e_consistentes(self):
        with patch.dict(os.environ, {}, clear=True):
            publisher = PublisherConfig.from_env()
            monitor = MonitorConfig.from_env()
            gate = GateConfig.from_env()

        self.assertEqual(publisher.radar_interval_seconds, 1200)
        self.assertEqual(publisher.offer_max_age_minutes, 120)
        self.assertEqual(publisher.recovered_max_age_minutes, 45)
        self.assertEqual(publisher.recovered_interval_seconds, 30)
        self.assertEqual(monitor.recovery_minutes, 30)
        self.assertEqual(monitor.recovery_limit, 500)
        self.assertEqual(monitor.capture_concurrency, 4)
        self.assertEqual(monitor.offer_max_age_minutes, publisher.offer_max_age_minutes)
        self.assertEqual(
            monitor.recovered_max_age_minutes,
            publisher.recovered_max_age_minutes,
        )
        self.assertEqual(gate.source_price_max_age_seconds, 300)
        self.assertEqual(gate.kabum_feed_max_age_seconds, 1800)

    def test_valores_invalidos_caem_em_defaults_seguros(self):
        env = {
            "RADAR_INTERVALO_PUBLICACAO": "invalido",
            "IDADE_MAXIMA_MINUTOS": "x",
            "INTERVALO_RECUPERADAS": "-9",
            "TG_RECUPERAR_MAX_MENSAGENS": "999999",
            "GATE_SOURCE_PRICE_MAX_AGE_SECONDS": "0",
        }
        with patch.dict(os.environ, env, clear=True):
            publisher = PublisherConfig.from_env()
            monitor = MonitorConfig.from_env()
            gate = GateConfig.from_env()

        self.assertEqual(publisher.radar_interval_seconds, 1200)
        self.assertEqual(publisher.offer_max_age_minutes, 120)
        self.assertEqual(publisher.recovered_interval_seconds, 5)
        self.assertEqual(monitor.recovery_limit, 2000)
        self.assertEqual(gate.source_price_max_age_seconds, 60)

    def test_concorrencia_de_captura_respeita_limites(self):
        with patch.dict(
            os.environ, {"TG_CAPTURA_CONCORRENCIA": "99"}, clear=True
        ):
            self.assertEqual(MonitorConfig.from_env().capture_concurrency, 16)
        with patch.dict(
            os.environ, {"TG_CAPTURA_CONCORRENCIA": "0"}, clear=True
        ):
            self.assertEqual(MonitorConfig.from_env().capture_concurrency, 1)

    def test_recuperadas_nunca_excedem_idade_maxima_geral(self):
        with patch.dict(
            os.environ,
            {
                "IDADE_MAXIMA_MINUTOS": "20",
                "IDADE_MAXIMA_RECUPERADAS_MINUTOS": "45",
                "TG_RECUPERAR_MINUTOS": "60",
            },
            clear=True,
        ):
            publisher = PublisherConfig.from_env()
            monitor = MonitorConfig.from_env()

        self.assertEqual(publisher.recovered_max_age_minutes, 20)
        self.assertEqual(monitor.recovered_max_age_minutes, 20)
        self.assertEqual(monitor.recovery_minutes, 20)

    def test_intervalo_radar_e_configuravel_com_limites(self):
        with patch.dict(
            os.environ,
            {"RADAR_INTERVALO_PUBLICACAO": "1800"},
            clear=True,
        ):
            self.assertEqual(
                PublisherConfig.from_env().radar_interval_seconds, 1800
            )
        with patch.dict(
            os.environ,
            {"RADAR_INTERVALO_PUBLICACAO": "1"},
            clear=True,
        ):
            self.assertEqual(
                PublisherConfig.from_env().radar_interval_seconds, 60
            )

    def test_parsers_normalizam_booleanos_inteiros_e_listas(self):
        env = {
            "BOOL_SIM": "sim",
            "BOOL_NAO": "não",
            "NUMERO": "999",
            "LISTA": " A, @Canal, ,B ",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(env_bool("BOOL_SIM"))
            self.assertFalse(env_bool("BOOL_NAO", True))
            self.assertEqual(env_int("NUMERO", 10, maximum=100), 100)
            self.assertEqual(env_csv("LISTA", lower=True), ("a", "@canal", "b"))

    def test_telegram_config_nao_inventa_credenciais(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_TOKEN": " token ",
                "TELEGRAM_CANAL": " @canal ",
                "TELEGRAM_ADMIN_CHAT": " -1001 ",
                "TG_API_ID": " 123 ",
                "TG_API_HASH": " hash ",
            },
            clear=True,
        ):
            cfg = TelegramConfig.from_env()
        self.assertEqual(cfg.token, "token")
        self.assertEqual(cfg.channel, "@canal")
        self.assertEqual(cfg.admin_chat, "-1001")
        self.assertEqual(cfg.api_id, "123")
        self.assertEqual(cfg.api_hash, "hash")

    def test_multicanal_fica_fechado_em_telegram_por_padrao(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                DistributionConfig.from_env().active_destinations,
                ("telegram",),
            )
        with patch.dict(
            os.environ, {"RADAR_DESTINOS_ATIVOS": "telegram,instagram"}, clear=True
        ):
            self.assertIn(
                "destinos externos ainda não possuem adaptador ativo",
                validate_operational_config(),
            )

    def test_snapshot_expoe_estado_sem_expor_segredos(self):
        secret = "token-ultrassecreto"
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_TOKEN": secret,
                "TELEGRAM_CANAL": "@canal",
                "TELEGRAM_ADMIN_CHAT": "-1001",
                "TG_API_ID": "123",
                "TG_API_HASH": "hash-secreto",
                "TG_CHATS": "-1002,-1003",
            },
            clear=True,
        ):
            snapshot = operational_snapshot()
        rendered = repr(snapshot)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("hash-secreto", rendered)
        self.assertTrue(snapshot["telegram"]["bot_token_configured"])
        self.assertEqual(snapshot["monitor"]["configured_chats"], 2)

    def test_validacao_aponta_apenas_nomes_das_configuracoes(self):
        with patch.dict(os.environ, {}, clear=True):
            problems = validate_operational_config()
        self.assertIn("TELEGRAM_TOKEN ausente", problems)
        self.assertIn("TELEGRAM_CANAL ausente", problems)
        self.assertIn("TG_CHATS vazio", problems)


if __name__ == "__main__":
    unittest.main()
