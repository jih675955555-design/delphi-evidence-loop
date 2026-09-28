// Browser walk through the collection stage: list → open → play and watch STT write → consent → ① 추출 → new
// hypothesis → undo. Called by scripts/collect_ui_check.sh against a keyless temp copy. Exits non-zero on any failure.
const { chromium } = require('playwright');

const BASE = process.argv[2] || 'http://127.0.0.1:8061';
const SHOTS = process.argv[3] || '.';
const EXPECT_CER = process.env.EXPECT_CER;
let failed = 0;
function check(ok, what) { console.log(`${ok ? '[OK]  ' : '[FAIL]'} ${what}`); if (!ok) failed++; }
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const page = await browser.newPage({ viewport: { width: 1320, height: 1000 } });
  await page.route(url => !url.href.startsWith(BASE), r => r.abort());   // fonts etc. — the page must work offline
  const go = async path => { await page.goto(BASE + path, { waitUntil: 'domcontentloaded' }); return page.content(); };
  const banner = async () => (await page.locator('.banner').allTextContents()).join(' ');

  // 1 · every console page has the nav item
  for (const path of ['/console', '/notes', '/claims', '/hypotheses', '/checklist', '/collect']) {
    await go(path);
    check(await page.locator('nav.nav a[href="/collect"]', { hasText: '현장 수집' }).count() === 1, `nav «현장 수집» on ${path}`);
  }
  await go('/notes');
  check(await page.locator('main a[href="/collect"]').count() >= 1, '/notes links to /collect');

  // 2 · the list
  let html = await go('/collect');
  for (const sid of ['FS-01', 'FS-02', 'FS-03', 'FS-04']) check(html.includes(`/collect/${sid}`), `/collect lists ${sid}`);
  check(/출처 GitHub/.test(html) && html.includes('data/field_scripts'), 'source line names GitHub and data/field_scripts');
  check(html.includes('지금 3회/2인'), 'FS-01 intent shows the current count 3회/2인');
  await page.screenshot({ path: `${SHOTS}/collect.png`, fullPage: true });

  // 3 · open FS-01 and wait for the audio
  await go('/collect/FS-01');
  await page.waitForFunction(() => { const a = document.getElementById('player'); return a && a.duration > 10; }, null, { timeout: 15000 });
  const duration = await page.evaluate(() => document.getElementById('player').duration);
  check(duration > 10, `audio loaded (${duration.toFixed(1)} s)`);
  const replay = page.locator('[data-listen="replay"]');
  check(await replay.count() === 1 && await page.locator('[data-listen="live"]').count() === 0, 'without a key only the replay button is offered');

  // 4 · play and watch the transcript grow
  const t0 = Date.now();
  await replay.click();
  const lens = [];
  for (const at of [2000, 6000, 12000]) {
    await sleep(Math.max(0, t0 + at - Date.now()));
    lens.push(await page.evaluate(() => document.getElementById('heard').textContent.length));
    if (at === 2000) {
      check(await page.evaluate(() => document.getElementById('player').currentTime) > 1, 'audio is playing after 2 s');
      check((await page.locator('#lstatus').textContent()).includes('모델 호출 없음'), 'status says «모델 호출 없음»');
    }
    if (at === 6000) await page.screenshot({ path: `${SHOTS}/collect_FS-01_listening.png`, fullPage: true });
  }
  check(lens[0] < lens[1] && lens[1] < lens[2], `transcript grows while the audio plays (${lens.join(' → ')} chars)`);

  // 5 · audio ends → DONE within 3 s → the page reloads with CER and the consent form
  let ended = false;
  for (let i = 0; i < 400 && !ended; i++) {
    try { ended = await page.evaluate(() => document.getElementById('player').ended); } catch (e) { ended = true; }   // reloaded
    if (!ended) await sleep(100);
  }
  const tEnd = Date.now();
  let done = false;
  while (!done && Date.now() - tEnd < 3000) {
    const j = await (await page.request.get(`${BASE}/collect/FS-01/listen.json`)).json();
    done = j.status === 'DONE';
    if (!done) await sleep(150);
  }
  check(done, 'listen.json is DONE within 3 s of the audio ending');
  await page.waitForSelector('input[name="consent_by"]', { timeout: 10000 });
  await page.screenshot({ path: `${SHOTS}/collect_FS-01_heard.png`, fullPage: true });
  const text = await page.locator('main').textContent();
  const m = text.match(/CER ([0-9]+[.][0-9])%/);
  check(!!m && (!EXPECT_CER || m[1] === EXPECT_CER), `CER line shown (${m && m[1]}%, code: ${EXPECT_CER}%)`);

  // 6 · consent: an empty name is refused (browser and server), a name saves the note
  check(await page.evaluate(() => !document.querySelector('input[name="consent_by"]').form.checkValidity()), 'empty consent blocked by the form');
  await page.request.post(`${BASE}/run/collect/save`, { form: { script: 'FS-01', consent_by: '' }, maxRedirects: 0 });
  await go('/collect/FS-01');
  check((await banner()).includes('거부'), `server refuses an empty consent («${(await banner()).slice(0, 60)}»)`);
  await page.fill('input[name="consent_by"]', '평가자');
  await Promise.all([page.waitForNavigation(), page.click('button:has-text("녹음 동의를 확인했습니다")')]);
  check((await banner()).includes('FN-2026-0921-01') && (await banner()).includes('평가자'), `saved: «${(await banner()).slice(0, 90)}»`);

  // 7 · no second save
  check(await page.locator('input[name="consent_by"]').count() === 0 && (await page.content()).includes('수집됨'), 'consent form gone, «수집됨» shown');
  await page.request.post(`${BASE}/run/collect/save`, { form: { script: 'FS-01', consent_by: '평가자' }, maxRedirects: 0 });
  await go('/collect/FS-01');
  check((await banner()).includes('이미 수집한 대본입니다 — FN-2026-0921-01'), 'a second save is refused');

  // 8 · next step: the existing extraction, and what it changed
  await go('/collect');
  await Promise.all([page.waitForNavigation({ timeout: 60000 }), page.click('button:has-text("다음 단계 — ① 추출 실행")')]);
  check((await banner()).includes('새 가설 1개'), `extraction: «${(await banner()).slice(0, 100)}»`);
  const effect = await page.locator('#effect ~ table').first().textContent().catch(() => '');
  check(effect.includes('3회/2인') && effect.includes('→'), `#effect shows before 3회/2인 → after (${effect.replace(/\s+/g, ' ').slice(0, 80)})`);
  const hypLink = page.locator('#effect ~ table a[href^="/hypotheses/HYP-"]').first();
  const href = await hypLink.getAttribute('href').catch(() => null);
  check(href === '/hypotheses/HYP-006', `effect links the new hypothesis (${href})`);
  await page.locator('#effect').scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${SHOTS}/collect_effect.png`, fullPage: true });

  // 9 · the existing flow continues from the new hypothesis
  html = await go('/hypotheses/HYP-006');
  check(html.includes('② 근거 교차검증 실행') && html.includes('FN-2026-0921-01'), 'HYP-006 page quotes FN-2026-0921-01 and offers ② 근거 교차검증');

  // 10 · the note carries its origin
  html = await go('/notes');
  check(html.includes('대본 수집 · FS-01'), '/notes shows «대본 수집 · FS-01»');

  // 11 · undo for the next viewer
  await go('/collect');
  await Promise.all([page.waitForNavigation(), page.click('button:has-text("수집 되돌리기")')]);
  check(!(await page.locator('main table').first().textContent()).includes('수집됨'), `undo: «${(await banner()).slice(0, 90)}»`);
  await go('/notes');
  check(await page.locator('.note').count() === 12, 'undo: /notes has the original 12 notes');

  await browser.close();
  console.log(failed ? `UI CHECK FAILED (${failed})` : 'UI CHECK OK');
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
