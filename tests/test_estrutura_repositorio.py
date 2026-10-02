import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class EstruturaRepositorioTests(unittest.TestCase):
    def test_legado_ml_nao_fica_na_raiz(self):
        for name in (
            "radar_mercadolivre_v6.py",
            "mercadolivre_auth.py",
            "teste_mercadolivre.py",
        ):
            self.assertFalse((ROOT / name).exists(), name)

        archive = ROOT / "archive" / "mercadolivre"
        self.assertTrue((archive / "radar_mercadolivre_v6.py").is_file())
        self.assertTrue((archive / "mercadolivre_auth_oauth.py").is_file())
        self.assertTrue((archive / "teste_mercadolivre_real.py").is_file())

    def test_testes_automatizados_ficam_em_tests(self):
        self.assertFalse((ROOT / "test_imagem_marca.py").exists())
        self.assertFalse((ROOT / "test_radar_expansao.py").exists())
        self.assertTrue((ROOT / "tests" / "test_imagem_marca.py").is_file())
        self.assertTrue((ROOT / "tests" / "test_radar_expansao.py").is_file())

    def test_radar_nao_importa_publicador_principal(self):
        modules = imported_modules(ROOT / "radar_shopee_continuo.py")
        self.assertNotIn("bot_ofertas_revisao", modules)
        self.assertIn("publicacao_oferta", modules)

    def test_entrypoints_nao_importam_archive(self):
        for name in (
            "monitor_ofertas.py",
            "bot_ofertas_revisao.py",
            "radar_shopee_continuo.py",
            "radar_kabum.py",
            "publicacao_oferta.py",
        ):
            with self.subTest(name=name):
                modules = imported_modules(ROOT / name)
                self.assertFalse(
                    any(module.startswith("archive") for module in modules),
                    modules,
                )

    def test_deploy_da_vps_fica_versionado(self):
        systemd = ROOT / "deploy" / "systemd"
        for name in (
            "radar-monitor.service",
            "radar-publicador.service",
            "radar-shopee.service",
            "radar-backup.service",
            "radar-backup.timer",
        ):
            self.assertTrue((systemd / name).is_file(), name)

        backup = (ROOT / "deploy" / "bin" / "radar-backup.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("publicacoes.sqlite3", backup)
        self.assertIn("destinos_confirmados.json", backup)
        self.assertNotIn("fila_ofertas_v2.jsonl", backup)
        self.assertNotIn("fila_shopee_api.jsonl", backup)
        self.assertNotIn("ofertas_para_revisar.jsonl", backup)


if __name__ == "__main__":
    unittest.main()
