"""Test per main.py: validate_config (validazione condivisa tra `init` e
`run`) e la personalizzazione testuale di config/sources.example.yaml
usata da `init`.
"""
import os
import tempfile
import unittest

from click.testing import CliRunner

from main import cli, validate_config, _render_config_from_example


VALID_CFG = {
    "project": {"user_agent": "MioBot/1.0 (+https://esempio.it; contatto: me@esempio.it)"},
    "seeds": ["https://it.wikipedia.org/wiki/Test"],
    "deny_domains": [],
    "crawl": {"max_pages_per_domain": 20},
}


class TestValidateConfig(unittest.TestCase):
    def test_valid_config_has_no_errors(self):
        self.assertEqual(validate_config(dict(VALID_CFG)), [])

    def test_missing_user_agent(self):
        cfg = dict(VALID_CFG)
        cfg["project"] = {}
        errors = validate_config(cfg)
        self.assertTrue(any("user_agent" in e for e in errors))

    def test_placeholder_user_agent(self):
        cfg = dict(VALID_CFG)
        cfg["project"] = {"user_agent": "Bot (contatto: tuo-email@example.com)"}
        errors = validate_config(cfg)
        self.assertTrue(any("placeholder" in e for e in errors))

    def test_empty_seeds(self):
        cfg = dict(VALID_CFG)
        cfg["seeds"] = []
        errors = validate_config(cfg)
        self.assertTrue(any("seed" in e.lower() for e in errors))

    def test_seeds_not_a_list(self):
        cfg = dict(VALID_CFG)
        cfg["seeds"] = "https://esempio.it"
        errors = validate_config(cfg)
        self.assertTrue(any("lista di URL" in e for e in errors))

    def test_seed_not_a_url(self):
        cfg = dict(VALID_CFG)
        cfg["seeds"] = ["non-e-un-url"]
        errors = validate_config(cfg)
        self.assertTrue(any("Seed non valido" in e for e in errors))

    def test_deny_domains_not_a_list(self):
        cfg = dict(VALID_CFG)
        cfg["deny_domains"] = "esempio.it"
        errors = validate_config(cfg)
        self.assertTrue(any("deny_domains" in e for e in errors))

    def test_max_pages_per_domain_invalid(self):
        cfg = dict(VALID_CFG)
        cfg["crawl"] = {"max_pages_per_domain": -5}
        errors = validate_config(cfg)
        self.assertTrue(any("max_pages_per_domain" in e for e in errors))

    def test_not_a_dict_at_all(self):
        errors = validate_config(["not", "a", "dict"])
        self.assertEqual(len(errors), 1)


EXAMPLE_TEXT = """project:
  name: "esempio-dataset"
  user_agent: "ScrapeLLM-Bot/0.1 (+https://github.com/tuo-utente/ScrapeLLM; contatto: tuo-email@example.com)"

seeds:
  - https://it.wikipedia.org/wiki/Intelligenza_artificiale
  - https://it.wikipedia.org/wiki/Etica_dei_dati
"""


class TestRenderConfigFromExample(unittest.TestCase):
    def test_substitutes_all_three_fields(self):
        out = _render_config_from_example(
            EXAMPLE_TEXT,
            project_name="il-mio-progetto",
            user_agent="MioBot/1.0 (+https://esempio.it; contatto: me@esempio.it)",
            first_seed="https://esempio.it/pagina",
        )
        self.assertIn('name: "il-mio-progetto"', out)
        self.assertIn('user_agent: "MioBot/1.0 (+https://esempio.it; contatto: me@esempio.it)"', out)
        self.assertIn("seeds:\n  - https://esempio.it/pagina\n", out)
        self.assertNotIn("esempio-dataset", out)
        self.assertNotIn("tuo-email@example.com", out)
        self.assertNotIn("Intelligenza_artificiale", out)

    def test_empty_arguments_keep_example_defaults(self):
        out = _render_config_from_example(EXAMPLE_TEXT, None, None, None)
        self.assertEqual(out, EXAMPLE_TEXT)

    def test_empty_first_seed_keeps_default_seeds(self):
        out = _render_config_from_example(EXAMPLE_TEXT, "nome", "un-user-agent", "")
        self.assertIn("Intelligenza_artificiale", out)
        self.assertIn('name: "nome"', out)


class TestInitCommand(unittest.TestCase):
    def _write_example(self):
        os.makedirs("config", exist_ok=True)
        with open("config/sources.example.yaml", "w", encoding="utf-8") as f:
            f.write(EXAMPLE_TEXT)

    def test_init_interactive_writes_valid_config(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._write_example()
            result = runner.invoke(
                cli, ["init"],
                input="il-mio-progetto\nMioBot/1.0 (+https://esempio.it; contatto: me@esempio.it)\nhttps://esempio.it/pagina\n",
            )
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(os.path.exists("config/sources.yaml"))
            with open("config/sources.yaml", encoding="utf-8") as f:
                written = f.read()
            self.assertIn('name: "il-mio-progetto"', written)
            self.assertIn("Configurazione valida", result.output)

    def test_init_non_interactive_copies_example_and_reports_placeholder_errors(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._write_example()
            result = runner.invoke(cli, ["init", "--non-interactive"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(os.path.exists("config/sources.yaml"))
            # the untouched example still has the placeholder user_agent
            self.assertIn("placeholder", result.output)

    def test_init_refuses_to_overwrite_without_force(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._write_example()
            with open("config/sources.yaml", "w", encoding="utf-8") as f:
                f.write("gia' presente\n")
            result = runner.invoke(cli, ["init", "--non-interactive"])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("--force", result.output)

    def test_init_force_overwrites(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            self._write_example()
            with open("config/sources.yaml", "w", encoding="utf-8") as f:
                f.write("gia' presente\n")
            result = runner.invoke(cli, ["init", "--non-interactive", "--force"])
            self.assertEqual(result.exit_code, 0, result.output)
            with open("config/sources.yaml", encoding="utf-8") as f:
                written = f.read()
            self.assertNotEqual(written, "gia' presente\n")

    def test_init_missing_example_file_errors_out(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["init"])
            self.assertNotEqual(result.exit_code, 0)


class TestRunUsesSharedValidation(unittest.TestCase):
    def test_run_exits_early_with_invalid_config(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("bad.yaml", "w", encoding="utf-8") as f:
                f.write("project:\n  user_agent: null\nseeds: []\n")
            result = runner.invoke(cli, ["run", "--config", "bad.yaml"])
            self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
