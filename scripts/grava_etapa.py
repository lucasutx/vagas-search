"""Grava você fazendo uma etapa (teste da Gupy etc.) no navegador do bot, para depois o bot aprender a fazer sozinho.

Uso:  .venv\\Scripts\\python.exe scripts\\grava_etapa.py --id 123 --nome stack [--minutos 45]

Abre o link da etapa (Retornos, id), faz o login na Gupy se pedir e deixa a janela para você. A cada mudança de tela
grava em data/learn/gravacao/<nome>/: o texto visível, campos/opções e o que está marcado (telas.jsonl), o HTML de
cada tela nova (telaN.html) e cada clique/tecla que você dá (acoes.jsonl). Para quando a janela fecha, quando a página
diz que terminou, ou no tempo-limite. Nada sai da sua máquina.
"""
import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vagas.cli import Runtime  # noqa: E402
from vagas.config import DATA_DIR  # noqa: E402

LISTEN_JS = r"""
(() => {
  if (window.__vagasRecOn) return; window.__vagasRecOn = true;
  const desc = (el) => {
    if (!el || !el.tagName) return {};
    const chain = []; let e = el;
    for (let i = 0; e && e.tagName && i < 5; i++, e = e.parentElement)
      chain.push(e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/).slice(0, 3).join('.') : ''));
    return {text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 200),
            role: el.getAttribute('role') || '', type: el.type || '', name: el.name || '', checked: !!el.checked,
            value: (el.value || '').toString().slice(0, 200), chain};
  };
  const send = (kind, el, extra) => { try { window.__vagasRec(JSON.stringify(Object.assign({kind, url: location.href, at: Date.now(), el: desc(el)}, extra || {}))); } catch (e) {} };
  document.addEventListener('click', ev => send('click', ev.target), true);
  document.addEventListener('change', ev => send('change', ev.target), true);
  document.addEventListener('keydown', ev => { if (['Enter', ' ', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Tab'].includes(ev.key)) send('key', ev.target, {key: ev.key}); }, true);
})();
"""

SNAP_JS = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const labelOf = (e) => {
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return l.innerText.trim(); }
    const l = e.closest('label'); if (l) return l.innerText.trim();
    return (e.getAttribute('aria-label') || e.placeholder || e.name || '').trim();
  };
  const fields = [...document.querySelectorAll('input, select, textarea')].filter(e => e.type !== 'hidden' && (vis(e) || e.type === 'radio' || e.type === 'checkbox')).slice(0, 200).map(e => ({
    tag: e.tagName.toLowerCase(), type: e.type || '', name: e.name || '', label: labelOf(e).slice(0, 200), checked: !!e.checked,
    value: (e.tagName === 'SELECT' ? (e.selectedOptions[0] || {}).text : e.value || '').toString().slice(0, 200),
    options: e.tagName === 'SELECT' ? [...e.options].map(o => o.text).slice(0, 60) : undefined}));
  const aria = [...document.querySelectorAll('[role=radio],[role=checkbox],[role=option],[role=switch],[role=slider],[aria-pressed]')].filter(vis).slice(0, 200).map(e => ({
    role: e.getAttribute('role') || 'button', text: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 200),
    checked: e.getAttribute('aria-checked') || e.getAttribute('aria-selected') || e.getAttribute('aria-pressed') || '', value: e.getAttribute('aria-valuenow') || ''}));
  const marked = [...document.querySelectorAll('[class*=selected],[class*=checked],[class*=active],[data-state=checked],[data-checked=true]')].filter(vis).slice(0, 80)
    .map(e => (e.innerText || '').trim().slice(0, 160)).filter(Boolean);
  const buttons = [...document.querySelectorAll('button, a, [role=button]')].filter(vis).map(b => (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 80)).filter(Boolean).slice(0, 40);
  const body = (document.body && document.body.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
  return {url: location.href, title: document.title, body: body.slice(0, 6000), fields, aria, marked, buttons};
}
"""

DONE_RE = re.compile(r"teste (finalizado|conclu[ií]do)|obrigad[oa] por (responder|participar)|respostas (foram )?enviadas|etapa conclu[ií]da|voc[eê] finalizou", re.I)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, required=True, help="id da etapa em Retornos (email_responses)")
    ap.add_argument("--nome", required=True, help="pasta da gravação (ex.: stack)")
    ap.add_argument("--minutos", type=float, default=45)
    a = ap.parse_args()
    out = DATA_DIR / "learn" / "gravacao" / a.nome
    out.mkdir(parents=True, exist_ok=True)
    telas, acoes = out / "telas.jsonl", out / "acoes.jsonl"

    rt = Runtime(stop_grace=5)
    try:
        row = rt.db.conn.execute("SELECT action_url, company FROM email_responses WHERE id=?", (a.id,)).fetchone()
        if not row or not row[0]:
            print("etapa sem link"); return
        url = row[0]
        ctx = rt.browser.context

        def on_action(source, payload):
            with acoes.open("a", encoding="utf-8") as f:
                f.write(payload + "\n")
        ctx.expose_binding("__vagasRec", on_action)
        ctx.add_init_script(LISTEN_JS)

        page = rt.browser.page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        for _ in range(25):
            time.sleep(1)
            if re.search(r"signin|login", page.url or "") or page.locator(".expandable-card__header-text").count():
                break
        if re.search(r"signin|login", page.url or ""):
            from types import SimpleNamespace
            from vagas.appliers.generic import GupyApplier
            GupyApplier()._login_via_linkedin(SimpleNamespace(profile=rt.profile, browser=rt.browser), page)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        print(f"PRONTO — pode fazer a etapa da {row[1]} na janela do bot. Gravando em {out}", flush=True)

        last_sig, last_body, n = "", "", 0
        deadline = time.time() + a.minutos * 60
        done_at = None
        while time.time() < deadline:
            pages = [p for p in ctx.pages if not p.is_closed()]
            if not pages:
                print("janela fechada"); break
            for p in pages:
                for fr in p.frames:
                    try:
                        fr.evaluate(LISTEN_JS)   # páginas já abertas antes do init script / iframes
                    except Exception:
                        pass
            p = pages[-1]
            try:
                snap = p.evaluate(SNAP_JS)
            except Exception:
                time.sleep(1); continue
            sig = hashlib.md5(json.dumps([snap["url"], snap["body"][:3000], snap["fields"], snap["aria"], snap["marked"]],
                                         ensure_ascii=False).encode()).hexdigest()
            if sig != last_sig:
                last_sig = sig
                snap["at"] = datetime.now().isoformat(timespec="seconds")
                if snap["body"][:1500] != last_body[:1500]:
                    n += 1
                    snap["tela"] = n
                    try:
                        (out / f"tela{n}.html").write_text(p.content()[:600000], encoding="utf-8")
                    except Exception:
                        pass
                    print(f"tela {n}: {snap['url'][:90]} · {snap['body'][:120]!r}", flush=True)
                last_body = snap["body"]
                with telas.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(snap, ensure_ascii=False) + "\n")
                if DONE_RE.search(snap["body"]) and done_at is None:
                    done_at = time.time()
                    print("a página diz que terminou — gravo mais 20 s e paro", flush=True)
            if done_at and time.time() - done_at > 20:
                break
            time.sleep(0.7)
        print(f"FIM — {n} telas gravadas em {out}", flush=True)
    finally:
        rt.close()


if __name__ == "__main__":
    main()
