"""Responsive layout, focus, contrast and motion checks against an isolated PC app.

Run: python tests/e2e/ui_e2e.py [screenshot folder]
Requires the existing Edge/Chrome browser, not an extra browser dependency.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402

DEBUG = 9396
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("Edge or Chrome is required for these UI checks")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    def check(name, result):
        print(("ok   " if result else "FAIL ") + name)
        if not result:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="afp-ui-") as folder:
        work = Path(folder)
        settings = base.make_settings(work)
        (work / "workspace" / "chat-preview.txt").write_text("Read-only chat preview\n" + "long-output-" * 40, encoding="utf-8")
        settings.unlock.agent_port = 8797
        running = start(settings, quiet=True)
        proc = None
        page = None
        try:
            accounts = running.app.state.accounts
            accounts.setup_owner("Preview Owner", "correct horse")
            url, admin = f"http://127.0.0.1:{base.PORT}", f"http://127.0.0.1:{base.ADMIN}"
            for _ in range(50):
                try:
                    httpx.get(url + "/healthz").raise_for_status()
                    break
                except httpx.HTTPError:
                    time.sleep(0.2)
            token = httpx.post(url + "/pair/claim", json={
                "code": accounts.new_pairing_code()[0], "name": "Preview phone",
            }).json()["token"]
            cookie = httpx.post(admin + "/admin/login", json={
                "username": "Preview Owner", "password": "correct horse",
            }, headers={"X-Admin": "1"}).cookies.get("afp_admin")
            proc = subprocess.Popen([
                str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}",
                f"--user-data-dir={work / 'profile'}", "--no-first-run",
                "--disable-gpu", "about:blank",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2.5)
            page = base.Page(DEBUG)
            page.call("Page.enable")
            page.call("Network.enable")
            page.call("Network.setCookie", name="afp_admin", value=cookie, domain="127.0.0.1", path="/")
            page.call("Page.addScriptToEvaluateOnNewDocument", source=
                f"localStorage.setItem('afp_token', {json.dumps(token)});"
                "localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1');"
                "const interval=window.setInterval; window.setInterval=(fn,ms,...args)=>interval(fn,ms===20000?500:ms,...args);"
                "window.uiErrors=[]; addEventListener('error',e=>uiErrors.push(e.message));"
                "addEventListener('unhandledrejection',e=>uiErrors.push(String(e.reason)));")

            def size(width, height=860):
                page.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

            def shot(name):
                time.sleep(0.4)
                (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

            def key(key, shift=False):
                page.call("Input.dispatchKeyEvent", type="keyDown", key=key, code=key, modifiers=8 if shift else 0,
                          windowsVirtualKeyCode=9 if key == "Tab" else 27)
                page.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=key)

            def fits(selector):
                return page.js(f"""(() => {{
                    const e = document.querySelector({json.dumps(selector)});
                    return e && e.scrollWidth <= e.clientWidth + 1;
                }})()""")

            size(412)
            page.call("Page.navigate", url=url)
            page.wait("!!document.querySelector('.hm-tiles')", 20, "Home readings")
            page.wait("document.querySelector('#home').innerText.includes('Get set up')", 10, "setup card")
            check("animated chat background stays hidden on Home", page.js("getComputedStyle(document.querySelector('.chat-ambient')).display === 'none'"))
            # Keep the same actual button focused across several changing status responses.
            page.js("""(() => {
                const fetch = window.fetch;
                window.uiReading = 10;
                window.fetch = async (...args) => {
                    const response = await fetch(...args);
                    if (String(args[0]).endsWith('/status') && response.ok) {
                        const body = await response.json();
                        body.cpu_percent = ++window.uiReading;
                        return new Response(JSON.stringify(body), {status:200, headers:{'Content-Type':'application/json'}});
                    }
                    return response;
                };
                window.homeAction = document.querySelector('#tabbar .tab[data-tab=settings]');
                homeAction.focus();
            })()""")
            page.wait("uiReading > 12", 25, "three live telemetry updates")
            check("Home preserves the focused control during live updates", page.js("homeAction.isConnected && document.activeElement === homeAction"))
            check("live readings still update", page.js("document.querySelector('.hm-tile b').textContent === uiReading + '%'"))
            check("Home has a clear page heading", page.js("document.querySelectorAll('#home h1').length === 1"))
            check("main phone controls have at least 44px targets", page.js("""[...document.querySelectorAll('#home button, #tabbar button')]
                .filter(e=>e.getClientRects().length).every(e=>e.getBoundingClientRect().height >= 44)"""))
            shot("phone-home.png")

            for theme in ("dark", "light"):
                page.js(f"document.documentElement.dataset.theme = '{theme}'")
                for width in (320, 375, 768, 1280):
                    size(width, 568 if width == 320 else 860)
                    page.js("document.documentElement.dataset.text = 'l'")
                    check(f"Home fits {width}px with large text in {theme}", fits("#home") and fits("#app"))
                size(412)
                page.js("document.documentElement.dataset.text = 'm'")
                check(f"secondary text has 4.5:1 contrast in {theme}", page.js("""(() => {
                    const style = getComputedStyle(document.documentElement);
                    const rgb = value => {
                        const el = document.createElement('span'); el.style.color=value; document.body.append(el);
                        const parts = getComputedStyle(el).color.match(/[\\d.]+/g).slice(0,3).map(Number); el.remove(); return parts;
                    };
                    const luminance = color => rgb(color).map(v=>{v/=255; return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;})
                        .reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
                    const bg = luminance(style.getPropertyValue('--surface'));
                    return ['--muted','--faint'].every(name=>{
                        const fg=luminance(style.getPropertyValue(name));
                        return (Math.max(bg,fg)+.05)/(Math.min(bg,fg)+.05)>=4.5;
                    });
                })()"""))
                if theme == "light":
                    shot("phone-light.png")

            page.js("document.documentElement.dataset.theme='dark'; document.querySelector('#tabbar .tab[data-tab=settings]').click()")
            page.wait("!!document.querySelector('.sheet')", 5, "settings")
            check("opening settings moves focus into the dialog", page.js("document.querySelector('.sheet').contains(document.activeElement)"))
            page.js("""(() => {
                const buttons=[...document.querySelectorAll('.sheet button')].filter(e=>e.getClientRects().length);
                buttons[buttons.length-1].focus();
            })()""")
            key("Tab")
            check("Tab wraps to the first dialog control", page.js("document.activeElement === document.querySelector('.sheet button')"))
            key("Tab", shift=True)
            check("Shift+Tab stays in the dialog", page.js("document.querySelector('.sheet').contains(document.activeElement)"))
            shot("phone-settings.png")
            key("Escape")
            page.wait("!document.querySelector('.sheet-wrap')", 5, "settings to close")
            check("closing settings restores keyboard focus", page.js("document.activeElement.matches('#tabbar .tab[data-tab=settings]')"))

            size(1280, 900)
            page.js("document.querySelector('.newchat').click()")
            check("desktop New chat opens Chat from Home", page.js("document.body.classList.contains('tab-chat') && document.querySelector('.field').offsetParent !== null"))
            check("desktop chat fits its main column", fits(".main"))
            check("every welcome suggestion has a visible label", page.js("""[...document.querySelectorAll('.hero .sug')]
                .every(e=>e.textContent.trim().length > 0) && document.querySelectorAll('.hero .sug').length >= 3"""))
            check("kinetic type is decorative and does not intercept input", page.js("""(() => {
                const bg=document.querySelector('.chat-ambient');
                return getComputedStyle(bg).display === 'block' && bg.getAttribute('aria-hidden') === 'true'
                    && getComputedStyle(bg).pointerEvents === 'none' && bg.querySelectorAll('.chat-type-row').length === 3;
            })()"""))
            check("background has moving type lanes and glow layers", page.js("""(() => {
                const bg=document.querySelector('.chat-ambient'), rows=[...bg.querySelectorAll('.chat-type-row')];
                return getComputedStyle(bg, '::before').animationName === 'chat-glow'
                    && getComputedStyle(bg, '::after').animationName === 'chat-glow'
                    && rows[0].dataset.flow === 'left' && rows[1].dataset.flow === 'right'
                    && getComputedStyle(rows[0]).animationName === 'chat-type-left'
                    && getComputedStyle(rows[1]).animationName === 'chat-type-right'
                    && rows[0].innerText.includes('LISTEN') && rows[0].innerText.includes('RESPOND')
                    && rows[1].innerText.includes('HUMAN INTENT') && rows[1].innerText.includes('YOUR CONTROL')
                    && rows[2].innerText.includes('CAREFUL BY DEFAULT')
                    && getComputedStyle(rows[0]).animationIterationCount === 'infinite';
            })()"""))
            check("kinetic type actually moves over time", page.js("""(async () => {
                const row=document.querySelector('.chat-type-row'), start=getComputedStyle(row).transform;
                await new Promise(resolve=>setTimeout(resolve, 900));
                return start !== getComputedStyle(row).transform;
            })()"""))
            check("background follows the selected accent", page.js("""(() => {
                const type=document.querySelector('.chat-type'), root=document.documentElement;
                const original=root.dataset.accent, first=getComputedStyle(type).color;
                root.dataset.accent='violet'; const next=getComputedStyle(type).color;
                root.dataset.accent=original; return first !== next;
            })()"""))
            check("background responds to the assistant's active state", page.js("""(() => {
                const type=document.querySelector('.chat-type'), body=document.body, original=body.dataset.phase;
                type.style.transition='none'; body.dataset.phase='idle'; const idle=parseFloat(getComputedStyle(type).opacity);
                body.dataset.phase='thinking'; const active=parseFloat(getComputedStyle(type).opacity);
                body.dataset.phase=original; type.style.removeProperty('transition'); return active > idle;
            })()"""))
            for theme in ("dark", "light"):
                page.js(f"document.documentElement.dataset.theme='{theme}'")
                for width in (320, 412, 1280):
                    size(width, 568 if width == 320 else 900)
                    check(f"animated chat fits {width}px in {theme}", fits(".main") and fits("#scroller"))
                    if width != 320:
                        shot(f"chat-ambient-{'phone' if width == 412 else 'desktop'}-{theme}.png")
            page.js("document.documentElement.dataset.theme='dark'")
            size(1280, 900)
            page.js("[...document.querySelectorAll('.hero .sug')].find(e=>e.textContent.trim()==='Battery').click()")
            page.wait("document.querySelector('#thread').innerText.includes('battery status')", 10, "welcome suggestion to send its prompt")
            check("welcome suggestions submit the intended request", True)
            page.wait("!!document.querySelector('#thread .answer')", 15, "completed reply")
            reply = (
                "# Your PC\n\n**Ready** for your next request.\n"
                "- Read files\n- Review changes before approving\n\n"
                "| Item | Details |\n| --- | --- |\n"
                f"| Long path | {'workspace-folder-' * 18} |\n\n"
                "```txt\n<img src=x onerror=alert(1)>\n"
                f"{'long-code-line-' * 30}\n```\n\n"
                "[Help](https://example.com) and [unsafe](javascript:alert).\n\n"
                + "\n\n".join(f"Paragraph {i}: all response content stays available." for i in range(20))
                + "\n\nEnd of full reply."
            )
            page.js("""(() => {
                const fetch = window.fetch;
                window.chatFetch = fetch;
                window.chatPolls = 0;
                window.chatJob = document.querySelector('#thread .turn').dataset.id;
                window.fetch = async (...args) => {
                    const response = await fetch(...args);
                    if (String(args[0]).endsWith('/jobs') && response.ok && !args[1]?.method) {
                        const jobs = await response.json();
                        for (const job of jobs) if (job.id === chatJob) {
                            job.outputs = ['PC status\\nCPU: 10%\\nMemory: 25%', REPLY];
                            chatPolls++;
                        }
                        return new Response(JSON.stringify(jobs), {status:200, headers:{'Content-Type':'application/json'}});
                    }
                    return response;
                };
            })()""".replace("REPLY", json.dumps(reply)))
            page.wait("!!document.querySelector('.answer h2')", 15, "structured response")
            check("replies render headings, lists, emphasis and tables", page.js("""(() => {
                const answer = document.querySelectorAll('.answer')[1];
                return !!answer.querySelector('h2') && answer.querySelectorAll('li').length === 2
                    && !!answer.querySelector('strong') && !!answer.querySelector('table');
            })()"""))
            check("plain reply line breaks are preserved", page.js("""(() => {
                const p=document.querySelector('.answer p');
                return p.textContent === 'PC status\\nCPU: 10%\\nMemory: 25%'
                    && getComputedStyle(p).whiteSpace === 'pre-wrap';
            })()"""))
            check("code and HTML-like text remain literal", page.js("""document.querySelector('.answer pre code').textContent.includes('<img src=x onerror=alert(1)>')
                && !document.querySelector('.answer img')"""))
            check("reply links allow only safe web URLs", page.js("""[...document.querySelectorAll('.answer a')]
                .every(a=>a.protocol === 'https:' && a.rel.includes('noopener')) && document.querySelectorAll('.answer a').length === 1"""))
            for theme in ("dark", "light"):
                page.js(f"document.documentElement.dataset.theme='{theme}'")
                for width in (320, 375, 768, 1280):
                    size(width, 568 if width == 320 else 860)
                    page.js("document.documentElement.dataset.text='l'")
                    check(f"chat responses fit {width}px with large text in {theme}", fits(".main") and fits("#scroller")
                          and page.js("[...document.querySelectorAll('.bot-main')].every(e=>e.scrollWidth<=e.clientWidth+1)"))
                    check(f"wide reply content scrolls locally at {width}px in {theme}", page.js("""[...document.querySelectorAll('.answer .cv-scroll, .answer pre')]
                        .every(e=>getComputedStyle(e).overflowX === 'auto' && e.clientWidth <= e.closest('.bot-main').clientWidth)"""))
                size(412)
                page.js("document.documentElement.dataset.text='m'")
                page.js("document.querySelector('#scroller').scrollTop=0")
                shot(f"chat-response-{theme}.png")
                size(1280)
                shot(f"chat-response-desktop-{theme}.png")
            page.js("document.documentElement.dataset.theme='dark'")
            check("response spacing is compact without clipping content", page.js("""(() => {
                const answer=document.querySelectorAll('.answer')[1], style=getComputedStyle(answer);
                return answer.innerText.includes('End of full reply.') && style.maxHeight === 'none'
                    && parseFloat(getComputedStyle(document.querySelector('#thread')).gap)<=20;
            })()"""))
            page.js("window.chatNode=document.querySelector('.answer'); window.chatNext=chatPolls+2")
            page.wait("chatPolls>=chatNext", 15, "unchanged reply polls")
            check("unchanged replies keep their DOM instead of replaying entrances", page.js("chatNode === document.querySelector('.answer')"))
            for app_motion in (False, True):
                if app_motion:
                    page.js("document.documentElement.dataset.motion='off'")
                else:
                    page.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
                check(f"{'app' if app_motion else 'system'} reduced motion limits chat entrances", page.js("""(() => {
                    const answer=document.querySelector('.answer'); answer.classList.add('reveal');
                    const duration=parseFloat(getComputedStyle(answer).animationDuration);
                    answer.classList.remove('reveal'); return duration<.01;
                })()"""))
                check(f"{'app' if app_motion else 'system'} reduced motion stops the animated background", page.js("""(() => {
                    const bg=document.querySelector('.chat-ambient');
                    return [getComputedStyle(bg, '::before'), getComputedStyle(bg, '::after'),
                        ...[...bg.querySelectorAll('.chat-type-row')].map(e=>getComputedStyle(e))]
                        .every(style=>style.animationName === 'none');
                })()"""))
                page.js("delete document.documentElement.dataset.motion")
                page.call("Emulation.setEmulatedMedia", features=[])
            page.js("window.fetch=chatFetch")
            page.js("""(() => {
                const field=document.querySelector('.field');
                field.value='create chat-layout.txt with compact preview';
                field.dispatchEvent(new Event('input', {bubbles:true}));
                document.querySelector('.send').click();
            })()""")
            page.wait("!!document.querySelector('.plan[data-state=wait]')", 15, "approval plan")
            for width in (320, 375, 768, 1280):
                size(width)
                check(f"approval details and controls fit {width}px", page.js("""(() => {
                    const plan=document.querySelector('.plan[data-state=wait]');
                    return plan.scrollWidth<=plan.clientWidth+1 && plan.innerText.includes('chat-layout.txt')
                        && [...plan.querySelectorAll('.actions button')].every(e=>e.getBoundingClientRect().height>=44)
                        && plan.querySelectorAll('.actions button').length === 2;
                })()"""))
            size(412)
            shot("chat-approval.png")
            page.js("[...document.querySelectorAll('.plan .actions button')].find(e=>e.textContent==='Reject').click()")
            page.wait("!!document.querySelector('.chip.rejected')", 10, "rejected plan")
            check("reject still prevents the planned write", not (work / "workspace" / "chat-layout.txt").exists())
            page.js("""(() => {
                const field=document.querySelector('.field'); field.value='read chat-preview.txt';
                field.dispatchEvent(new Event('input', {bubbles:true})); document.querySelector('.send').click();
            })()""")
            page.wait("!!document.querySelector('.out')", 15, "file output")
            for width in (320, 1280):
                size(width)
                check(f"literal file output and Copy fit {width}px", page.js("""(() => {
                    const out=document.querySelector('.out'), copy=document.querySelector('.out-copy');
                    return out.textContent.includes('Read-only chat preview\\n') && out.textContent.endsWith('long-output-')
                        && out.scrollWidth<=out.clientWidth+1 && parseFloat(getComputedStyle(out).paddingRight)>=58
                        && copy.getBoundingClientRect().height>=44;
                })()"""))
            page.js("document.querySelector('.home-btn').click()")
            check("animated background stops being displayed outside Chat", page.js("getComputedStyle(document.querySelector('.chat-ambient')).display === 'none'"))
            page.js("document.querySelector('#tabbar [data-tab=\"files\"]').click()")
            page.wait("!!document.querySelector('.fb-row')", 10, "file browser")
            page.js("document.querySelector('.fb-main').click()")
            page.wait("!!document.querySelector('.fb-tools input')", 10, "folder toolbar")
            check("file toolbar controls share a 44px minimum height", page.js("""[...document.querySelectorAll('.fb-tools input, .fb-tools select, .fb-tools button')]
                .filter(e=>e.getClientRects().length).every(e=>e.getBoundingClientRect().height>=44)"""))
            for width in (320, 768, 1280):
                size(width)
                check(f"file browser fits {width}px", fits("#canvas"))
            size(412)
            shot("phone-files.png")
            check("phone screens have no browser errors", page.js("uiErrors.length === 0"))

            page.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
            check("reduced motion disables lengthy transitions", page.js("""[...document.querySelectorAll('.canvas, .sheet, .hm-btn')]
                .every(e=>getComputedStyle(e).animationDuration.split(',').every(v=>parseFloat(v)<.01))"""))

            page.call("Page.navigate", url=admin)
            page.wait("!!document.querySelector('.dgrid .mini-qr svg')", 15, "PC dashboard")
            for width in (320, 768, 1280, 1440):
                size(width, 900)
                check(f"PC overview fits {width}px", fits("html") and fits(".dmain"))
                if width == 320:
                    check("all six navigation labels remain visible", page.js("""[...document.querySelectorAll('.rnav span')]
                        .every(e=>getComputedStyle(e).display !== 'none')"""))
                if width == 1440:
                    shot("desktop-overview.png")
            for view in ("phones", "unlock", "connection", "activity", "settings"):
                size(320)
                page.js(f"document.querySelector('.rnav[data-view={view}]').click()")
                check(f"PC {view} fits a narrow window", fits("html") and fits(".dmain"))
            size(1280)
            page.js("document.querySelector('.rnav[data-view=phones]').click()")
            page.js("[...document.querySelectorAll('.device button')].find(b=>b.textContent.includes('Rename')).click()")
            page.wait("!!document.querySelector('.modal input')", 5, "rename dialog")
            key("Tab", shift=True)
            check("desktop dialog traps reverse tab", page.js("document.querySelector('.modal').contains(document.activeElement) && document.activeElement.tagName === 'BUTTON'"))
            key("Escape")
            check("desktop dialog closes with Escape", page.js("!document.querySelector('.modal')"))
            check("PC dashboard has no browser errors", page.js("uiErrors.length === 0"))
            page.js("document.querySelector('.who button').click()")
            page.wait("!!document.querySelector('.input input[type=password]')", 10, "PC sign-in")
            page.js("document.querySelector('.input input[type=password]').focus()")
            page.call("Input.insertText", text="preview-password")
            check("sign-in provides one custom password visibility control", page.js("document.querySelectorAll('.input .eye').length === 1"))
            check("Edge's duplicate reveal control is suppressed", page.js("""[...document.styleSheets].some(sheet =>
                [...sheet.cssRules].some(rule => rule.selectorText === '.input input[type="password"]::-ms-reveal' && rule.style.display === 'none'))"""))
            shot("desktop-password.png")
            page.js("document.querySelector('.eye').click()")
            check("Show password reveals the unchanged value", page.js("""document.querySelector('input[name=password]').type === 'text'
                && document.querySelector('input[name=password]').value === 'preview-password'
                && document.querySelector('.eye').getAttribute('aria-label') === 'Hide password'"""))
            page.js("document.querySelector('.eye').click()")
            check("Hide password masks it again", page.js("""document.querySelector('input[name=password]').type === 'password'
                && document.querySelector('.eye').getAttribute('aria-label') === 'Show password'"""))
        finally:
            if page:
                page.call("Browser.close")
                page.ws.close()
            if proc:
                proc.wait(timeout=10)
            running.stop()
            for server in running.servers:
                server.thread.join(timeout=5)
                if server.thread.is_alive():
                    raise RuntimeError("UI fixture server did not stop")
            running.app.state.manager.db.close()
    print("\n" + ("ALL PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
