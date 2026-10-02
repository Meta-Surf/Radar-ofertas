import unittest

import radar_health as health


class RadarHealthTests(unittest.TestCase):
    def test_configured_requires_all_values(self):
        env = {"A": "x", "B": "y", "C": ""}
        self.assertTrue(health.configured(env, "A", "B"))
        self.assertFalse(health.configured(env, "A", "C"))

    def test_render_contains_operational_sections(self):
        report = {
            "services": {
                "radar-monitor.service": "active",
                "radar-publicador.service": "active",
                "radar-shopee.service": "active",
            },
            "database": {
                "queue_total": 10,
                "queue_sources": {"shopee_api": 8, "kabum_feed": 2},
                "ml_quarantined": 1,
                "ml_circuit_seconds": 0,
                "ml_failures": {"ML_HTTP_403": 1},
                "reservation_states": {"uncertain": 2},
            },
            "kabum_stock": {"known_stock": 0, "products": 100, "coverage_pct": 0.0},
            "integrations": {
                "shopee": True,
                "mercadolivre": True,
                "awin_kabum": True,
                "amazon_creators": False,
            },
            "backup": {"name": "backup.tar.gz", "age_hours": 2.0, "size_mb": 50.0},
            "critical": [],
            "warnings": ["Amazon: Creators API aguardando credenciais"],
        }
        text = health.render(report)
        self.assertIn("Serviços:", text)
        self.assertIn("Fila radar: 10", text)
        self.assertIn("Reservas: uncertain=2", text)
        self.assertIn("amazon_creators=PENDENTE", text)
        self.assertIn("KaBuM estoque: 0/100", text)


if __name__ == "__main__":
    unittest.main()
