// Bridge between the local jev_ext_loop.py script and the browser.
//
// The script runs a WebSocket server on 127.0.0.1; this worker connects to it and carries out its
// commands. Two rules keep it contained:
//   1. The server must present the token from token.js (written by `jev_ext_loop.py --setup`)
//      before any command is accepted.
//   2. Commands only ever touch tabs this bridge opened itself. The user's own tabs are never read
//      or acted on.

importScripts('token.js'); // defines JEV_TOKEN

const SERVER = 'ws://127.0.0.1:8765';
let ws = null;

async function myTabs() {
  return (await chrome.storage.session.get('tabs')).tabs || [];
}

async function requireMine(tabId) {
  if (!(await myTabs()).includes(tabId)) throw new Error('not a tab this bridge opened');
}

function connect() {
  if (ws && ws.readyState <= 1) return;
  let trusted = false;
  try { ws = new WebSocket(SERVER); } catch { ws = null; return; }
  ws.onmessage = async (ev) => {
    const m = JSON.parse(ev.data);
    if (!trusted) {
      if (m.hello === JEV_TOKEN) { trusted = true; ws.send(JSON.stringify({ hello: 'ok' })); } else ws.close();
      return;
    }
    let out;
    try { out = { id: m.id, ok: true, result: await handle(m) }; } catch (e) { out = { id: m.id, ok: false, error: String(e.message || e) }; }
    ws.send(JSON.stringify(out));
  };
  ws.onclose = () => { ws = null; };
  ws.onerror = () => {};
}

// The worker sleeps when idle; the alarm wakes it to look for the script's server.
chrome.alarms.create('jev-connect', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();

async function inTab(tabId, func, args = []) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return res.result;
}

async function handle(m) {
  if (m.cmd === 'ping') return 'pong';
  if (m.cmd === 'version') return chrome.runtime.getManifest().version;
  if (m.cmd === 'reload') { setTimeout(() => chrome.runtime.reload(), 200); return 'reloading'; } // pick up edited files
  if (m.cmd === 'open') {
    let tab;
    try {
      tab = await chrome.tabs.create({ url: m.url, active: false });
    } catch {
      const win = await chrome.windows.create({ url: m.url, focused: false }); // no browser window was open
      tab = win.tabs[0];
    }
    const groupId = await chrome.tabs.group({ tabIds: [tab.id] });
    await chrome.tabGroups.update(groupId, { title: m.title, color: 'purple' });
    await chrome.storage.session.set({ tabs: [...(await myTabs()), tab.id] });
    return { tabId: tab.id };
  }
  await requireMine(m.tabId);
  if (m.cmd === 'status') {
    const t = await chrome.tabs.get(m.tabId);
    return { status: t.status, url: t.url, title: t.title };
  }
  if (m.cmd === 'perceive') return inTab(m.tabId, perceive);
  if (m.cmd === 'hide_overlays') return inTab(m.tabId, hideOverlays); // only with the loop's --hide-overlays
  if (m.cmd === 'show') { // bring the tab in front, for when the user has to step in
    const t = await chrome.tabs.update(m.tabId, { active: true });
    await chrome.windows.update(t.windowId, { focused: true });
    return true;
  }
  if (m.cmd === 'click') return inTab(m.tabId, clickElement, [m.index]);
  if (m.cmd === 'type') return inTab(m.tabId, typeInto, [m.index, m.text, m.submit]);
  if (m.cmd === 'choose') return inTab(m.tabId, chooseOption, [m.index, m.option]);
  if (m.cmd === 'screenshot') return screenshot(m.tabId, m.mode);
  if (m.cmd === 'scroll') return inTab(m.tabId, () => { scrollBy(0, innerHeight * 0.8); return true; });
  if (m.cmd === 'close') { await chrome.tabs.remove(m.tabId); return true; }
  throw new Error('unknown command ' + m.cmd);
}

// A picture of the tab, for the visual hand-off. Taken through the debugger protocol, so the tab can stay in the
// background. Edge shows a "started debugging this browser" bar while it is attached: a second or so.
async function screenshot(tabId, mode = 'auto') {
  const target = { tabId };
  const withTimeout = (p, ms) => Promise.race([p, new Promise((_, reject) => setTimeout(() => reject(new Error('timed out')), ms))]);
  const opts = { format: 'jpeg', quality: 70 };
  await chrome.debugger.attach(target, '1.3');
  try {
    // A tab in a window that is covered or minimized is not painted, so a plain capture waits forever.
    // Capturing "beyond the viewport" makes the page lay out and paint for the capture itself.
    // Tested on Edge 157 with the window fully covered: 'beyond' returned in about a second, 'plain' timed out.
    const tries = mode === 'auto' ? ['beyond', 'plain'] : [mode];
    const errors = [];
    for (const t of tries) {
      try {
        if (t === 'plain') return { mode: t, data: (await withTimeout(chrome.debugger.sendCommand(target, 'Page.captureScreenshot', opts), 4000)).data };
        const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func: () => [innerWidth, innerHeight] });
        const [w, h] = result;
        const clip = { x: 0, y: 0, width: w, height: h, scale: 1 };
        const shot = await withTimeout(chrome.debugger.sendCommand(target, 'Page.captureScreenshot', { ...opts, clip, captureBeyondViewport: true }), 6000);
        return { mode: t, data: shot.data };
      } catch (e) { errors.push(t + ': ' + e.message); }
    }
    throw new Error('screenshot failed (' + errors.join('; ') + ')');
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

// ---- functions below run inside the page ----

// What a user could click or type into in the viewport, plus visible text as evidence.
// What is typed in a field is never read: a field is named by its label only.
function perceive() {
  document.querySelectorAll('[data-jev]').forEach(e => e.removeAttribute('data-jev'));
  const vw = innerWidth, vh = innerHeight, seen = new Set(), els = [], cands = [];
  let covered = 0;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 140);
  // The name of a control that is not a button: aria-label, a <label>, or the nearest short text around it.
  // A placeholder is a sample value ("AAPL", "100"), so it is only a fallback or a hint.
  const nameOf = (el) => {
    const lab = el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
    const by = el.getAttribute('aria-labelledby');
    const labelled = by ? by.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(n => n.innerText).join(' ') : '';
    let near = '';
    for (let p = el.parentElement, i = 0; p && i < 3 && !near; p = p.parentElement, i++) {
      const t = clean(p.innerText);
      if (t.length >= 2 && t.length <= 40) near = t;
    }
    return clean(el.getAttribute('aria-label') || labelled || (lab && lab.innerText) ||
                 (el.closest('label') && el.closest('label').innerText) || near);
  };
  const sel = 'a[href],button,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],input,textarea,select,' +
              '[role=combobox],[role=searchbox],[role=textbox],[role=checkbox],[role=radio],[role=switch]';
  for (const el of document.querySelectorAll(sel)) {
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || 'text').toLowerCase(), role = el.getAttribute('role');
    if (tag === 'input' && ['hidden', 'file', 'range', 'color', 'image'].includes(type)) continue;
    const isToggle = (tag === 'input' && (type === 'checkbox' || type === 'radio')) || ['checkbox', 'radio', 'switch'].includes(role);
    // A styled checkbox or radio often hides the real input and shows its label: judge visibility on the label.
    const box = (isToggle && tag === 'input' && el.getBoundingClientRect().width < 4 && (el.closest('label') ||
                 (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]')))) || el;
    const r = box.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;
    const cs = getComputedStyle(box);
    if (cs.visibility === 'hidden' || cs.display === 'none' || (+cs.opacity === 0 && box === el && !isToggle)) continue;
    const x = Math.min(Math.max(r.left + r.width / 2, 1), vw - 1), y = Math.min(Math.max(r.top + r.height / 2, 1), vh - 1);
    const top = document.elementFromPoint(x, y);
    if (top && top !== box && !box.contains(top) && !top.contains(box)) { covered++; continue; }
    const isButtonInput = tag === 'input' && ['submit', 'button', 'reset'].includes(type);
    const field = !isToggle && ((tag === 'input' && !isButtonInput) || tag === 'textarea' || ['searchbox', 'textbox'].includes(role));
    const item = {};
    let text;
    if (tag === 'select') {
      // A dropdown: its options and which one is chosen now are part of the page, like a button's label.
      text = nameOf(el) || clean(el.getAttribute('name'));
      item.role = 'select';
      item.options = [...el.options].map(o => clean(o.text)).slice(0, 60);
      item.value = el.selectedIndex >= 0 ? clean(el.options[el.selectedIndex].text) : '';
      item.choice = true;
    } else if (isToggle) {
      text = nameOf(el) || clean(el.innerText || el.getAttribute('title') || el.value);
      item.role = role || type;
      item.checked = tag === 'input' ? el.checked : el.getAttribute('aria-checked') === 'true';
    } else if (field) {
      const name = nameOf(el), ph = el.getAttribute('placeholder');
      text = clean(name ? (ph && !name.includes(ph) ? name + ' (e.g. ' + ph + ')' : name) : (ph || el.getAttribute('title') || el.getAttribute('name')));
      item.role = role || 'text field';
    } else {
      text = clean(isButtonInput ? el.value : (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title')));
      item.role = tag === 'a' ? 'link' : (role || tag);
      // A segmented control (Long | Short) is a row of buttons; which one is on is its state.
      const pressed = el.getAttribute('aria-pressed') || el.getAttribute('aria-selected') || (el.getAttribute('data-state') === 'active' ? 'true' : null);
      if (pressed !== null) item.selected = pressed === 'true';
    }
    if (!text) continue;
    item.text = text;
    item.box = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; // where it is, for recordings
    if (field) {
      const hint = text + ' ' + (el.getAttribute('name') || '') + ' ' + (el.id || ''), ac = el.getAttribute('autocomplete') || '';
      item.field = true;
      item.input_type = tag === 'textarea' ? 'textarea' : type;
      item.filled = (el.value || el.textContent || '').length > 0; // whether it holds something, never what
      item.secret = type === 'password' || /password|cc-|one-time-code/.test(ac) ||
        /סיסמ|password|passcode|אשראי|card.?num|cvv|cvc|תעודת זהות|ת\.ז|מספר זהות|ssn/i.test(hint);
      item.search = type === 'search' || ['searchbox', 'combobox'].includes(role) ||
        /חיפוש|חפש|search/i.test(hint) || !!el.closest('[role=search]');
    }
    cands.push({ item, el });
    if (cands.length >= 400) break;
  }
  // Controls that share a label (a "delete" button on every row of a table) are told apart by the text of
  // their row. Without it only one of them would be offered, and it would be whichever row came first.
  const keyOf = c => c.item.text + '|' + c.item.role;
  const count = new Map();
  for (const c of cands) count.set(keyOf(c), (count.get(keyOf(c)) || 0) + 1);
  for (const c of cands) {
    if (count.get(keyOf(c)) < 2) continue;
    const row = c.el.closest('tr,[role=row],li,article') ||
      [c.el.parentElement, c.el.parentElement?.parentElement, c.el.parentElement?.parentElement?.parentElement]
        .find(p => p && clean(p.innerText).length > c.item.text.length + 3);
    const context = row ? clean(row.innerText.replace(c.item.text, '')).slice(0, 110) : '';
    if (context) c.item.text = c.item.text + ' [row: ' + context + ']';
  }
  for (const c of cands) {
    const key = keyOf(c);
    if (seen.has(key)) continue; // still identical: only the first is offered
    seen.add(key);
    c.el.setAttribute('data-jev', els.length);
    els.push(c.item);
    if (els.length >= 200) break;
  }
  const texts = [], blocks = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n && blocks.size < 60; n = walker.nextNode()) {
    const p = n.parentElement;
    if (!n.nodeValue.trim() || !p || p.closest('script,style,noscript,input,textarea,[contenteditable]')) continue;
    const r = p.getBoundingClientRect();
    if (r.height < 4 || r.width < 4 || r.bottom <= 0 || r.top >= vh || getComputedStyle(p).visibility === 'hidden') continue;
    const block = p.closest('p,li,td,th,h1,h2,h3,h4,address,section,article,div') || p;
    blocks.set(block, ((blocks.get(block) || '') + ' ' + n.nodeValue).replace(/\s+/g, ' ').trim().slice(0, 240));
  }
  for (const t of blocks.values()) if (t.length > 2 && !texts.includes(t)) texts.push(t);
  const doc = document.scrollingElement;
  return { url: location.href, title: document.title, elements: els, page_text: texts, covered, viewport: [vw, vh],
           can_scroll_down: doc.scrollTop + vh < doc.scrollHeight - 20 };
}

// A consent wall or interstitial covering the page is hidden, never answered: no consent is given.
function hideOverlays() {
  const vw = innerWidth, vh = innerHeight, hidden = [];
  for (const [fx, fy] of [[.5, .5], [.25, .25], [.75, .25], [.25, .75], [.75, .75], [.5, .1], [.5, .9]]) {
    let el = document.elementFromPoint(vw * fx, vh * fy), wall = null;
    for (; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
      const r = el.getBoundingClientRect();
      if (getComputedStyle(el).position === 'fixed' && r.width * r.height > vw * vh * 0.5) wall = el;
    }
    if (wall) { hidden.push(wall.tagName.toLowerCase() + '.' + String(wall.className).slice(0, 40)); wall.style.setProperty('display', 'none', 'important'); }
  }
  return hidden;
}

function clickElement(index) {
  const el = document.querySelector('[data-jev="' + index + '"]');
  if (!el) throw new Error('element is gone');
  const link = el.closest('a[target]');
  if (link) link.removeAttribute('target'); // stay in this tab: a new tab would leave the bridge's group
  el.click();
  return true;
}

function typeInto(index, text, submit) {
  const el = document.querySelector('[data-jev="' + index + '"]');
  if (!el) throw new Error('field is gone');
  el.focus();
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value');
  if ('value' in el && setter) setter.set.call(el, text); else el.textContent = text;
  el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  if (!submit) return 'typed';
  const key = { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 };
  const unhandled = el.dispatchEvent(new KeyboardEvent('keydown', key));
  el.dispatchEvent(new KeyboardEvent('keypress', key));
  el.dispatchEvent(new KeyboardEvent('keyup', key));
  // A synthetic Enter does not trigger the browser's own form submission, so do that part by hand
  // unless the page's key handler already took the event.
  if (unhandled && el.form) { el.form.requestSubmit(); return 'typed, form submitted'; }
  return unhandled ? 'typed, Enter sent' : 'typed, Enter handled by the page';
}

// Pick an option of a <select> by its position, the way a user's pick would reach the page.
function chooseOption(index, option) {
  const el = document.querySelector('[data-jev="' + index + '"]');
  if (!el || el.tagName !== 'SELECT') throw new Error('dropdown is gone');
  if (option < 0 || option >= el.options.length) throw new Error('no such option');
  el.focus();
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
  setter.call(el, el.options[option].value); // the native setter, so frameworks that track value see the change
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return 'chose ' + el.options[el.selectedIndex].text.trim();
}
