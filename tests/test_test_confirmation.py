import sqlite3
import unittest
from unittest.mock import patch

from vagas import nextsteps


class FakeBot:
    def __init__(self):
        self.sent = []

    def send(self, text, buttons=None):
        self.sent.append(text)


class FakeBrowser:
    def page(self):
        return object()


class FakeDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE email_responses (id INTEGER PRIMARY KEY, kind TEXT, company TEXT, job_id TEXT, done INTEGER DEFAULT 0,
              auto_state TEXT, action_url TEXT, received_at TEXT, deadline TEXT, link TEXT, action TEXT, subject TEXT, prep TEXT);
            CREATE TABLE jobs (id TEXT, title TEXT);
            INSERT INTO jobs VALUES ('gupy:1', 'Desenvolvedor Java Pleno');
            INSERT INTO email_responses (id, kind, company, job_id, action_url, received_at)
              VALUES (1, 'comportamental', 'Globalweb', 'gupy:1', 'https://email.gupy.com.br/c/x', '2026-11-14T16:26:00');
        """)

    def row(self):
        return dict(self.conn.execute("SELECT done, auto_state FROM email_responses WHERE id=1").fetchone())


class ConfirmationTests(unittest.TestCase):
    def run_tick(self, fill_result, verified):
        db, bot = FakeDB(), FakeBot()
        with patch.object(nextsteps, "_bot", return_value=bot), \
             patch("vagas.fitcultural.open_test", return_value="test"), \
             patch("vagas.fitcultural.fill_test", return_value=fill_result), \
             patch("vagas.fitcultural.verify_done", return_value=verified) as v:
            nextsteps.fit_cultural(db, FakeBrowser(), {}, log=lambda *a: None)
        return db.row(), bot.sent, v

    def test_answered_and_verified_marks_waiting_and_notifies_success(self):
        row, sent, v = self.run_tick("done", True)
        v.assert_called_once()
        self.assertEqual(row["done"], 1)
        self.assertIn("conferido", row["auto_state"])
        self.assertIn("aguardando as próximas etapas", row["auto_state"])
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith("✅"))
        self.assertIn("Desenvolvedor Java Pleno", sent[0])

    def test_answered_but_not_verified_warns(self):
        row, sent, _ = self.run_tick("done", False)
        self.assertNotIn("aguardando", row["auto_state"])
        self.assertIn("confira", row["auto_state"])
        self.assertTrue(sent[0].startswith("⚠️"))

    def test_failure_stays_open(self):
        row, sent, v = self.run_tick("failed: questão 3: sem botão de avançar", None)
        v.assert_not_called()
        self.assertEqual(row["done"], 0)
        self.assertTrue(sent[0].startswith("⚠️"))


if __name__ == "__main__":
    unittest.main()


class ReuseScreenTests(unittest.TestCase):
    """Tela real da Gupy (Globalweb, 14/11): teste já feito noutra candidatura → reaproveitar o resultado."""
    PAGE = """<html><body><div id="app"><h2>Você já respondeu essa pesquisa!</h2><p>Deseja utilizar os resultados?</p>
      <p>Você respondeu esse teste no dia 09/11/2026.</p>
      <button id="repeatTestButton" type="button" onclick="document.body.innerText='QUESTÃO 1 DE 21'">Não, quero refazer</button>
      <button id="reuseTestButton" type="button" onclick="setTimeout(() => document.getElementById('app').innerHTML = '<h1>Pronto! Resultados enviados</h1>', 500)">Sim, quero utilizar</button>
    </div></body></html>"""

    def test_reuses_previous_result_instead_of_reading_as_already_done(self):
        from playwright.sync_api import sync_playwright
        from vagas import fitcultural
        with sync_playwright() as p:
            b = p.chromium.launch(channel="chrome", headless=True)
            try:
                pg = b.new_page(); pg.set_content(self.PAGE)
                with patch.object(fitcultural, "load_key", return_value={"use": True}):
                    res = fitcultural.fill_test(pg, log=lambda *a: None, kind="comportamental")
                self.assertEqual(res, "done")
                self.assertIn("Pronto", pg.inner_text("body"))
            finally:
                b.close()
