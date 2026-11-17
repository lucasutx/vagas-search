import unittest
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from vagas.appliers import flow
from vagas.config import Settings
from vagas.db import Job

ANCHOR = "https://www.recaptcha.net/recaptcha/enterprise/anchor?ar=1&k=x&co=y&size={size}&cb=z"
PAGE = """<html><body><h1>Verificar sua candidatura</h1>
<div data-testid="captcha"><iframe title="reCAPTCHA" width="304" height="78" src="{normal}"></iframe>
<textarea id="g-recaptcha-response" name="g-recaptcha-response"></textarea></div>
<iframe title="reCAPTCHA" width="256" height="60" src="{invisible}"></iframe>
<button type="submit" name="submit-application" disabled><span>Enviar sua candidatura</span></button>
</body></html>"""


class FinalCaptchaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.route("**/recaptcha/**", lambda r: r.fulfill(body="<html></html>", content_type="text/html"))

    def load(self, with_widget=True):
        normal = ANCHOR.format(size="normal") if with_widget else ANCHOR.format(size="invisible")
        self.page.set_content(PAGE.format(normal=normal, invisible=ANCHOR.format(size="invisible")))

    def test_visible_checkbox_with_locked_submit_is_a_gate(self):
        self.load()
        g = flow._captcha_gate(self.page)
        self.assertEqual((g["widget"], g["solved"], g["submitDisabled"]), (True, False, True))

    def test_invisible_badge_alone_is_not_a_gate(self):
        self.load(with_widget=False)
        self.assertFalse(flow._captcha_gate(self.page)["widget"])

    def test_waits_until_you_tick_it_then_continues(self):
        self.load()
        # simula a pessoa marcando: o token aparece e o botão libera depois de ~2 s
        self.page.evaluate("""() => setTimeout(() => { document.querySelector('textarea').value = 'x'.repeat(40);
                                                     document.querySelector('button').disabled = false; }, 2000)""")
        s = Settings(); s.limits.final_captcha_wait_minutes = 0.2
        ctx = SimpleNamespace(settings=s)
        job = Job(id="indeed:1", source="indeed", external_id="1", title="Dev", company="C", url="u")
        with patch("vagas.notify.notify") as n:
            self.assertTrue(flow._wait_final_captcha(ctx, self.page, self.page, job))
        n.assert_called_once()

    def test_gives_up_when_nobody_ticks(self):
        self.load()
        s = Settings(); s.limits.final_captcha_wait_minutes = 0.08
        ctx = SimpleNamespace(settings=s)
        job = Job(id="indeed:1", source="indeed", external_id="1", title="Dev", company="C", url="u")
        with patch("vagas.notify.notify"):
            self.assertFalse(flow._wait_final_captcha(ctx, self.page, self.page, job))


if __name__ == "__main__":
    unittest.main()
