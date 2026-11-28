"""Jarvis filling in pages in his own window (build plan item 7, part 1): what a page's controls are, what may never be
typed, what the owner's Send card shows, and one run's use of the window with tabs and a seal. No Qt.

    controls_script(gen), read_controls(raw, gen)   the page's visible controls, listed in an isolated world (it shares
                                    only the page's DOM, as agent.web's read_script does) and checked and capped here:
                                    at most MAX_CONTROLS, each a ref e<n> tied to that page's document by ``gen``
    listing(controls)               what the model is given of them (inside an untrusted block: agent.tools)
    field_refused(control, address, title), value_refused(text)   what is never typed: password, card, one-time-code
                                    and secret fields, file inputs, payment pages; secrets, card numbers (Luhn), IBANs and
                                    ID numbers. A fixed code and Jarvis's plain words, or None
    SendPlan, CardLine              the Send card: the button's words, every value Jarvis put into the page exactly, a
                                    value the run's taint set hits tagged amber, where the form goes, what the page set
                                    itself, and the note that only a click sends
    DriveCaps                       one task's pages, page actions and Send cards: reaching one asks "Keep going?"
                                    (agent.runner), as the turn and minute budget does
    DriveBrowser                    the seam agent.web's Browser grows for this (part 2 builds it in agent.browser)
    DriveSession(browser, engine, caps)   agent.web's WebSession with up to MAX_TABS tabs and the seal

The rules (owner's answers of 2026-11-23, A to C). The first time Jarvis types into a field or chooses an option, that
tab holds owner values: the window is sealed first (DriveBrowser.seal(cut=True): no new connection, and every one open
is cut), so nothing the page does can leave this PC. No page loads and no link opens while one holds them; only one tab
holds them at a time. It stays so until the owner's click on that page's Send card (press_approved lifts the seal for
the card's hosts alone and presses, in that order; the seal lifts whole only once a new page came, and is back as before
when none did), or until the tab is closed (close_tab destroys its page and profile, and only then lifts the seal). A
field takes one value a page: what Jarvis typed is never replaced, so the card can name every value the page may have
kept. A submit, Enter in a field, a button whose words commit (guard.COMMIT_WORDS and a few more), and any press while
the tab holds owner values put a Send card up; nothing else does, and nothing acts on that tab while it is up. Values
from the owner's mail, files or calendar may go only through that card, each tagged. A tab whose page opened signed in
as the owner (site_signins: a site he listed) is read only: every button there puts the Send card up, and a link whose
words would sign out, delete, unsubscribe or commit is not followed (D_SIGNED_IN); agent.browser refuses its form
submits and its writes unless the card's click presses them.

Nothing here logs anything; no repr holds a value, a label, a name, an address or a host.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from .. import site_signins
from ..actions import link_host, web_link_problem
from ..desktop import guard
from ..snapshots import LOCAL_BASE_URL
from ..format_chars import drop_invisible, invisible_char
from . import web
from .web import _JS_HELPERS, _JS_UNSEEN, NO_PAGE, Browser, Call, Opened, WebRefused, WebSession

MAX_CONTROLS = 150                # controls list_controls gives, at most
MAX_OPTIONS = 30                  # a list's options it names, at most
NAME_CAP = 120
VALUE_CAP = 200                   # of a field's current value in the listing
OPTION_CAP = 80
HINT_CAP = 400                    # a field's other words read for the secret check (name, id, placeholder ...)
TYPE_CAP = 2_000                  # characters type_text takes: the card shows them whole
TYPE_MS_PER_CHAR = 30             # typing the owner can watch ...
TYPE_MAX_MS = 3_000               # ... at most this long a field
TYPE_TICKS = 100                  # steps a field is typed in, at most
SETTLE_S = 25.0                   # after the approved press: a navigation, or this long
CLICK_SETTLE_S = 2.0              # after a free click
SEND_S = 40.0                     # the approved press and what follows it, all in
DESTROY_S = 10.0                  # a declined page's end
AFTER_SEND_CHARS = 2_000          # of the page after a send, given back
MAX_TABS = 3
DEFAULT_MAX_PAGES, DEFAULT_MAX_ACTIONS, DEFAULT_MAX_SENDS = 40, 150, 5
LINE_CAP = 120                    # a drive tool's spoken line (agent.tools)
TYPED_SEEN_MIN = 3                # a typed value this long is looked for in a spoken line
STORE = "__jarvisDrive"           # the isolated world's own name for the listed elements (no page script can see it)
MINE = "__jarvisMine"             # ... and for the elements Jarvis set, by their own keys (a new list never resets it)
PAGE_SET_SHOWN = 20               # values the page set itself a card shows, each in full; a form with more is refused
TAINT_TAG = "from your mail / files / calendar"
COPY_TAG = "a copy of what Jarvis typed"
HIDDEN_TAG = "hidden on the page"
CONSENT_TAG = "a box that agrees to terms: your Send agrees to them"

# Roles a control has (Control.role).
R_LINK, R_BUTTON, R_SUBMIT, R_TEXTBOX, R_TEXTAREA = "link", "button", "submit", "textbox", "textarea"
R_SELECT, R_CHECKBOX, R_RADIO, R_FILE = "select", "checkbox", "radio", "file"
ROLES = (R_LINK, R_BUTTON, R_SUBMIT, R_TEXTBOX, R_TEXTAREA, R_SELECT, R_CHECKBOX, R_RADIO, R_FILE)
TYPED_ROLES = frozenset({R_TEXTBOX, R_TEXTAREA})
CHOSEN_ROLES = frozenset({R_SELECT, R_CHECKBOX, R_RADIO})
TEXT_KINDS = frozenset({"text", "email", "tel", "url", "search", "number", "date", "datetime-local", "month", "week",
                        "time", "password", "color", "range", ""})

# Why a drive call did not happen (WebRefused.code; the logs' and Live's fixed words).
D_NO_DRIVE, D_STALE, D_NO_REF, D_SEALED, D_OTHER_TAB = "no_drive", "stale", "no_ref", "sealed", "other_tab_holds"
D_SECRET_FIELD, D_FILE_FIELD, D_PAYMENT, D_SECRET_VALUE = "secret_field", "file_field", "payment_page", "secret_value"
D_CARD_NUMBER, D_IBAN, D_NATIONAL_ID = "card_number", "iban", "national_id"
D_TABS, D_NO_TAB, D_CARD_UP, D_DECLINED, D_NO_SEAL = "tabs_full", "no_tab", "card_up", "declined", "no_seal"
D_WRONG_KIND, D_NO_OPTION, D_NO_LINK, D_BAD_INPUT = "wrong_kind", "no_option", "no_link", "bad_input"
D_TYPED_ONCE, D_MISSING, D_MOVED, D_OTHER_WINDOW = "typed_once", "field_gone", "page_moved", "other_window"
D_TOO_LONG, D_TOO_MANY, D_NOT_CLOSED = "too_long", "too_many_values", "not_closed"
D_SIGNED_IN = "signed_in_read_only"   # a link that would change the owner's account, on a page signed in as him
REFUSAL_CODES = frozenset({D_SECRET_FIELD, D_FILE_FIELD, D_PAYMENT, D_SECRET_VALUE, D_CARD_NUMBER, D_IBAN,
                           D_NATIONAL_ID})
# What came of an approved Send (SendOutcome.code).
S_SENT, S_CHANGED, S_GONE, S_UNKNOWN = "sent", "changed", "gone", "unknown"
S_NOT_SENT, S_STAYED = "not_sent", "stayed"   # no new page: the form did not go / the page kept it (sealed again)

NO_DRIVE_TEXT = "Filling in pages isn't available in this task. Say in your report what is left for the owner."
STALE_TEXT = "The page changed since list_controls, so nothing was done. Call list_controls again."
NO_REF_TEXT = "No control has that ref on this page. Use a ref list_controls gave for the page in this tab."
NO_LIST_TEXT = "Call list_controls first: refs come from it, for the page as it is now."
SEALED_TEXT = ("Tab {tab} holds what Jarvis typed, so nothing loads and no link opens until it is sent or discarded: "
               "press its submit (the owner gets a Send card), or close_tab {tab} to discard it.")
OTHER_TAB_TEXT = ("Tab {tab} holds what Jarvis typed already, and only one tab may: send it, or close_tab {tab}, before "
                  "you fill in another.")
SECRET_FIELD_TEXT = ("Jarvis never types into a password, code, card, account or ID number field. Leave it for the "
                     "owner and say so in your report.")
FILE_FIELD_TEXT = "Jarvis doesn't upload files. Leave it for the owner and say so in your report."
PAYMENT_TEXT = "Jarvis doesn't fill in or send payment pages. Leave it for the owner and say so in your report."
SECRET_VALUE_TEXT = "That looks like a password or a key, which Jarvis never types into a page. Leave it for the owner."
CARD_NUMBER_TEXT = "That looks like a card number, which Jarvis never types into a page. Leave it for the owner."
IBAN_TEXT = "That looks like a bank account number, which Jarvis never types into a page. Leave it for the owner."
NATIONAL_ID_TEXT = "That looks like an ID number, which Jarvis never types into a page. Leave it for the owner."
TABS_TEXT = f"{MAX_TABS} tabs are open, the most Jarvis keeps. Close one with close_tab first."
NO_TAB_TEXT = "There is no tab with that number. Tabs are numbered from 1 in the order they opened."
CARD_UP_TEXT = "A Send card for this page waits for the owner. End your turn now; what comes of it is your next message."
DECLINED_TEXT = "The owner said no to that send, so it is not tried again. Carry on without it, or say so in your report."
NO_SEAL_TEXT = ("Jarvis could not seal the window, so nothing was typed: nothing may be typed into a page that could "
                "send it. Say so in your report.")
WRONG_KIND_TEXT = {
    "type": "That control is not a text field. Use choose for lists and boxes, click for links and buttons.",
    "choose": "That control is not a list or a box. Use type_text for fields, click for links and buttons.",
    "click": "That control is a field. Use type_text or choose for it, press to submit it.",
    "press": "That is a link: use click for it.",
}
NO_OPTION_TEXT = "That list has no such option. Use one of the options list_controls named."
CHOOSE_INPUT_TEXT = 'Give "option" for a list, or "checked" for a box, one of them.'
NO_LINK_TEXT = "That link has no public https address, so Jarvis did not open it."
TYPED_ONCE_TEXT = ("Jarvis typed into that field on this page already, and each field takes one value: the page may "
                   "have kept the first. To change it, close_tab {tab} (it discards everything typed there) and start "
                   "again.")
MISSING_TEXT = ("A field Jarvis filled in on this page is gone from it, so the card could not show what would go and "
                "nothing was done. close_tab {tab} and start again.")
MOVED_TEXT = ("The page in this tab is no longer on the site Jarvis opened, so nothing was done. close_tab {tab}, or "
              "open the page again with open_page.")
OTHER_WINDOW_TEXT = "That button sends the form into another window, which Jarvis doesn't do. Leave it for the owner."
TOO_LONG_TEXT = ("A value in that form is longer than a card can show whole ({cap:,} characters), so Jarvis won't "
                 "send it. Leave it for the owner and say so in your report.")
TOO_MANY_TEXT = ("That form carries more values the page set itself than a card can show ({cap}), so Jarvis won't "
                 "send it. Leave it for the owner and say so in your report.")
NOT_CLOSED_TEXT = ("Tab {tab} could not be closed just now, so it still holds what Jarvis typed and the window stays "
                   "sealed. Call close_tab {tab} again.")
SIGNED_IN_LINK_TEXT = ("This page is signed in as the owner, and Jarvis only reads there: he doesn't follow a link "
                       "that would sign out, delete, unsubscribe or change something. Leave it for the owner and say "
                       "so in your report.")

# Words of a button that commits besides guard.COMMIT_WORDS: one more way of saying it.
_MORE_COMMIT = guard._phrases("subscribe", "sign up", "signup", "register", "apply", "book", "reserve", "donate",
                              "enrol", "enroll", "join", "unsubscribe", "accept", "agree", "authorise", "authorize",
                              "continue to payment", "next")
_SECRET_AUTOCOMPLETE_RE = re.compile(r"^(?:cc-.*|one-time-code|.*-password|password)$")
_REF_RE = re.compile(r"[eE]([1-9]\d{0,3})")
_DIGIT_RUN_RE = re.compile(r"(?<![\d])(?:\d[ \-]?){12,18}\d(?![\d])")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_ID_RES = (re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),                                    # a US social security number
           re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2} ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b"),              # a UK National Insurance number
           re.compile(r"(?<!\d)(?<!\d )\d{4} \d{4} \d{4}(?! ?\d)"))                          # a twelve-digit ID in fours


# --------------------------------------------------------------------------
# What Jarvis runs in the isolated world
# --------------------------------------------------------------------------

_JS_DRIVE = """
var FORM = HTMLFormElement.prototype;
function shown(el) {
  try {
    if (!ELEMENT.getClientRects.call(el).length) { return false; }
    var style = window.getComputedStyle(el);
    if (style.visibility !== 'visible' || style.display === 'none') { return false; }
    return !unseen(el);
  } catch (error) { return false; }
}
function tagOf(el) { return String(el.tagName || '').toLowerCase(); }
function typeOf(el) { return tagOf(el) === 'input' ? String(ELEMENT.getAttribute.call(el, 'type') || 'text').toLowerCase() : ''; }
function attr(el, name) { return String(ELEMENT.getAttribute.call(el, name) || ''); }
function formOf(el) {
  try { var found = el.form; return found && found instanceof HTMLFormElement ? found : null; } catch (error) { return null; }
}
function valueProto(el) {
  if (el instanceof HTMLTextAreaElement) { return HTMLTextAreaElement.prototype; }
  if (el instanceof HTMLSelectElement) { return HTMLSelectElement.prototype; }
  return HTMLInputElement.prototype;
}
function getValue(el) { return String(Object.getOwnPropertyDescriptor(valueProto(el), 'value').get.call(el) || ''); }
function setValue(el, value) { Object.getOwnPropertyDescriptor(valueProto(el), 'value').set.call(el, value); }
function isChecked(el) { return !!Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').get.call(el); }
function fire(el, name) { el.dispatchEvent(new Event(name, {bubbles: true})); }
function chosenText(el) {
  var index = el.selectedIndex;
  return index >= 0 && el.options[index] ? line(el.options[index]) || String(el.options[index].value || '') : '';
}
function valueOf(el) {
  var tag = tagOf(el), type = typeOf(el);
  if (tag === 'select') { return chosenText(el); }
  if (type === 'checkbox' || type === 'radio') { return isChecked(el) ? (getValue(el) || 'on') : ''; }
  return getValue(el);
}
function refOf(n) {
  var store = window['{store}'];
  if (!store || store.gen !== ARGS.gen) { return null; }
  var el = store.els[n - 1];
  return el && el.isConnected && shown(el) ? el : null;
}
function stale() { return JSON.stringify({stale: 1}); }
function mineStore() {
  var mine = window['{mine}'];
  if (!mine) { mine = {keys: new Map(), els: {}}; window['{mine}'] = mine; }
  return mine;
}
function hashOf(text) {
  var h = 0x811c9dc5;
  for (var i = 0; i < text.length; i++) { h = Math.imul(h ^ text.charCodeAt(i), 0x01000193) >>> 0; }
  return ('0000000' + h.toString(16)).slice(-8);
}
function submitterOf(el) {
  var tag = tagOf(el), type = typeOf(el);
  return (tag === 'button' && (attr(el, 'type') || 'submit').toLowerCase() === 'submit')
    || (tag === 'input' && (type === 'submit' || type === 'image'));
}
function howOf(el) {
  var form = formOf(el), tag = tagOf(el), type = typeOf(el), submitter = submitterOf(el);
  var field = tag === 'textarea' || (tag === 'input' && !submitter && ['checkbox', 'radio', 'button', 'reset', 'file']
    .indexOf(type) < 0);
  return form && submitter ? 'submit' : form && field ? 'enter' : field ? 'key' : 'click';
}
""".replace("{store}", STORE).replace("{mine}", MINE)

# What a press of a control would send, read the same way at the card, again at the owner's click, and once more in the
# very task that presses: every value whole in length and hash (only the first TYPE_CAP + 1 characters are given back),
# where the form goes as its submitter would send it, and the values of the elements Jarvis set, by their own keys.
_JS_FORM = f"""
function whole(value) {{ return {{value: value.slice(0, {TYPE_CAP + 1}), len: value.length, hash: hashOf(value)}}; }}
function own(el, name) {{
  var proto = el instanceof HTMLButtonElement ? HTMLButtonElement.prototype : HTMLInputElement.prototype;
  return String(Object.getOwnPropertyDescriptor(proto, name).get.call(el) || '');
}}
function snapshotOf(el, keys) {{
  var mine = mineStore(), values = {{}}, missing = [];
  for (var m = 0; m < keys.length; m++) {{
    var held = mine.els[keys[m]];
    if (!held || !held.isConnected) {{ missing.push(keys[m]); continue; }}
    values[String(keys[m])] = whole(valueOf(held));
  }}
  var form = formOf(el), how = howOf(el), fields = [], method = '', action = '', target = '';
  if (form) {{
    method = String(FORM.__lookupGetter__('method').call(form) || 'get');
    action = String(FORM.__lookupGetter__('action').call(form) || '');
    target = String(FORM.__lookupGetter__('target').call(form) || '');
    if (how === 'submit') {{
      if (ELEMENT.hasAttribute.call(el, 'formaction')) {{ action = own(el, 'formAction'); }}
      if (ELEMENT.hasAttribute.call(el, 'formmethod')) {{ method = own(el, 'formMethod') || method; }}
      if (ELEMENT.hasAttribute.call(el, 'formtarget')) {{ target = own(el, 'formTarget'); }}
    }}
    var inside = FORM.__lookupGetter__('elements').call(form);
    for (var f = 0; f < inside.length && fields.length < 400; f++) {{
      var item = inside[f], type = typeOf(item), named = attr(item, 'name');
      if (!named || ['submit', 'image', 'button', 'reset', 'file'].indexOf(type) >= 0 || tagOf(item) === 'button'
          || tagOf(item) === 'fieldset' || item.disabled) {{ continue; }}
      if ((type === 'checkbox' || type === 'radio') && !isChecked(item)) {{ continue; }}
      var field = whole(valueOf(item));
      field.k = mine.keys.get(item) || 0;
      field.name = named.slice(0, {NAME_CAP});
      field.hidden = type === 'hidden' || !shown(item);
      fields.push(field);
    }}
  }}
  return {{words: (line(el) || (tagOf(el) === 'input' ? getValue(el) : '')).slice(0, {NAME_CAP * 2}), form: !!form,
          how: how, method: method, action: action, target: target, fields: fields, mine: values, missing: missing,
          url: String(location.href), title: title().slice(0, {web.TITLE_CAP * 2})}};
}}
"""


def _script(op: str, args: dict[str, Any], body: str) -> str:
    """One drive script: its op as a mark (the fakes read it), its arguments as JSON on a line of their own, the helpers,
    then its body. It answers JSON text."""
    return (f"(function () {{/*drive:{op}*/\nvar ARGS = {json.dumps(args)};\n" + _JS_HELPERS + _JS_UNSEEN + _JS_DRIVE
            + _JS_FORM + body + "\n})()")


def controls_script(gen: str) -> str:
    """The page's visible controls, as JSON text, and the elements kept in the isolated world under ``gen`` for the
    refs. What the owner can't see is counted, never listed."""
    return _script("controls", {"gen": gen, "max": MAX_CONTROLS, "options": MAX_OPTIONS}, f"""
var found = all('a[href], button, input, select, textarea, [role="button"], [role="link"]');
var els = [], rows = [], forms = [], formIndex = new Map(), left = 0, above = 0, more = false;
function nameOf(el, tag, type, role) {{
  var seen = '';
  if (role === 'link' || role === 'button' || role === 'submit') {{
    seen = line(el) || (tag === 'input' ? getValue(el) : '');
  }}
  if (!seen && el.labels && el.labels.length) {{ seen = line(el.labels[0]); }}
  if (!seen && role !== 'select') {{ seen = attr(el, 'placeholder'); }}
  if (seen) {{ return [seen, 0]; }}
  var aria = attr(el, 'aria-label'), by = attr(el, 'aria-labelledby');
  if (!aria && by) {{ var named = DOC.getElementById.call(document, by.split(' ')[0]); aria = named ? line(named) : ''; }}
  return [aria || attr(el, 'title'), 1];
}}
for (var i = 0; i < found.length; i++) {{
  var el = found[i], tag = tagOf(el), type = typeOf(el), role = '';
  if (type === 'hidden' || el.disabled) {{ continue; }}
  if (!shown(el)) {{ left += 1; continue; }}
  if (ELEMENT.getBoundingClientRect.call(el).bottom < 0) {{ above += 1; continue; }}   // above where the page stands
  if (els.length >= ARGS.max) {{ more = true; break; }}
  if (tag === 'a') {{ role = 'link'; }}
  else if (tag === 'button') {{ role = (attr(el, 'type') || 'submit').toLowerCase() === 'submit' && formOf(el) ? 'submit' : 'button'; }}
  else if (tag === 'select') {{ role = 'select'; }}
  else if (tag === 'textarea') {{ role = 'textarea'; }}
  else if (tag === 'input') {{
    role = type === 'submit' || type === 'image' ? 'submit' : type === 'reset' || type === 'button' ? 'button'
      : type === 'checkbox' || type === 'radio' || type === 'file' ? type : 'textbox';
  }} else {{ role = attr(el, 'role') === 'link' && !attr(el, 'href') ? 'button' : attr(el, 'role') || 'button'; }}
  var name = nameOf(el, tag, type, role), form = formOf(el), number = 0;
  if (form) {{
    if (!formIndex.has(form)) {{
      forms.push([String(FORM.__lookupGetter__('method').call(form) || 'get'),
                  String(FORM.__lookupGetter__('action').call(form) || '')]);
      formIndex.set(form, forms.length);
    }}
    number = formIndex.get(form);
  }}
  var row = {{role: role, kind: role === 'textbox' ? type : '', name: name[0].slice(0, {NAME_CAP * 2}), sr: name[1],
             hints: [attr(el, 'name'), attr(el, 'id'), attr(el, 'placeholder'), attr(el, 'aria-label'),
                     attr(el, 'title')].join(' ').slice(0, {HINT_CAP}),
             auto: attr(el, 'autocomplete').slice(0, 80), required: !!el.required, form: number}};
  if (role === 'textbox' || role === 'textarea') {{ row.value = getValue(el).slice(0, {VALUE_CAP * 2}); }}
  if (role === 'select') {{
    row.value = chosenText(el).slice(0, {OPTION_CAP * 2});
    row.options = [];
    for (var o = 0; o < el.options.length && o < ARGS.options; o++) {{
      row.options.push((line(el.options[o]) || String(el.options[o].value || '')).slice(0, {OPTION_CAP * 2}));
    }}
  }}
  if (role === 'checkbox' || role === 'radio') {{ row.checked = isChecked(el); }}
  if (role === 'link') {{ row.href = String(el.href || '').slice(0, {web.URL_CAP + 1}); row.blank = attr(el, 'target') === '_blank'; }}
  els.push(el);
  rows.push(row);
}}
window['{STORE}'] = {{gen: ARGS.gen, els: els}};
return JSON.stringify({{gen: ARGS.gen, url: String(location.href), title: title().slice(0, {web.TITLE_CAP * 2}),
                       controls: rows, forms: forms, left: left, above: above, more: more}});""")


def _typing(text: str) -> tuple[int, int, int]:
    """(characters a step, milliseconds between steps, the whole time): ~TYPE_MS_PER_CHAR a character, TYPE_MAX_MS at
    most."""
    size = len(text)
    if not size:
        return 1, 0, 0
    total = min(TYPE_MS_PER_CHAR * size, TYPE_MAX_MS)
    ticks = min(size, TYPE_TICKS)
    return math.ceil(size / ticks), max(1, total // ticks), total


def type_script(gen: str, n: int, text: str, key: int = 0) -> str:
    """``text`` typed into control ``n`` as the owner can watch it: focus, the value emptied, then a few characters at
    a time through the native setter with an input event each, and a change event at the end. The element is kept in
    the MINE store under ``key`` first. It answers at once with how long the typing takes ({"stale": 1}: the ref is not
    this page's; {"again": 1}: Jarvis set that element already, and nothing was typed)."""
    per, step, total = _typing(text)
    return _script("type", {"gen": gen, "n": n, "text": text, "per": per, "step": step, "ms": total, "key": key}, """
var el = refOf(ARGS.n);
if (!el) { return stale(); }
var mine = mineStore();
if (mine.keys.has(el)) { return JSON.stringify({again: 1}); }
mine.keys.set(el, ARGS.key); mine.els[ARGS.key] = el;
var text = String(ARGS.text), at = 0;
HTML.focus.call(el);
setValue(el, ''); fire(el, 'input');
function next() {
  if (!el.isConnected) { return; }
  at = Math.min(text.length, at + ARGS.per);
  setValue(el, text.slice(0, at)); fire(el, 'input');
  if (at < text.length) { window.setTimeout(next, ARGS.step); } else { fire(el, 'change'); }
}
if (text.length) { window.setTimeout(next, ARGS.step); } else { fire(el, 'change'); }
return JSON.stringify({ok: 1, ms: ARGS.ms});""")


def choose_script(gen: str, n: int, option: str | None = None, checked: bool | None = None, key: int = 0) -> str:
    """A list's option chosen by its words or value, or a box ticked or not, with input and change events. The element
    is kept in the MINE store under ``key``, or the key it has already (the answer's "key")."""
    return _script("choose", {"gen": gen, "n": n, "option": option, "checked": checked, "key": key}, """
var el = refOf(ARGS.n);
if (!el) { return stale(); }
var mine = mineStore(), key = mine.keys.has(el) ? mine.keys.get(el) : ARGS.key;
if (tagOf(el) === 'select') {
  var wanted = String(ARGS.option || '').trim().toLowerCase(), at = -1;
  for (var o = 0; o < el.options.length; o++) {
    var said = (line(el.options[o]) || '').toLowerCase(), value = String(el.options[o].value || '').toLowerCase();
    if (said === wanted || value === wanted) { at = o; break; }
  }
  if (at < 0) { return JSON.stringify({missing: 1}); }
  mine.keys.set(el, key); mine.els[key] = el;
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex').set.call(el, at);
} else {
  mine.keys.set(el, key); mine.els[key] = el;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set.call(el, !!ARGS.checked);
}
fire(el, 'input'); fire(el, 'change');
return JSON.stringify({ok: 1, value: valueOf(el), key: key});""")


def click_script(gen: str, n: int) -> str:
    """A button clicked (never a link: Jarvis opens a link's address himself, as open_page does)."""
    return _script("click", {"gen": gen, "n": n}, """
var el = refOf(ARGS.n);
if (!el) { return stale(); }
HTML.click.call(el);
return JSON.stringify({ok: 1});""")


def press_script(gen: str, n: int, mine: Sequence[int] = (), expect: str = "") -> str:
    """The press the owner's Send allowed: a submit button submits its form with itself as the submitter, Enter in a
    field submits its form, and anything else is clicked. First, in the same task, what the press would send is read
    once more (form_script's very answer, ``expect``): anything else answers {"changed": 1} and nothing is pressed. The
    answer says how it pressed and whether the form's submit event came (``fired``: none when the form is invalid) and
    a script of the page took it over (``prevented``). Only ever run by DriveBrowser.press_approved."""
    return _script("press", {"gen": gen, "n": n, "mine": list(mine), "expect": expect}, """
var el = refOf(ARGS.n);
if (!el) { return stale(); }
if (JSON.stringify(snapshotOf(el, ARGS.mine)) !== ARGS.expect) { return JSON.stringify({changed: 1}); }
var form = formOf(el), how = howOf(el), seen = null;
function noted(event) { if (!seen && event.target === form) { seen = event; } }
window.addEventListener('submit', noted, true);
try {
  if (how === 'submit') { FORM.requestSubmit.call(form, el); }
  else if (how === 'enter') { FORM.requestSubmit.call(form); }
  else if (how === 'key') {
    ['keydown', 'keypress', 'keyup'].forEach(function (name) {
      el.dispatchEvent(new KeyboardEvent(name, {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
    });
  } else { HTML.click.call(el); }
} finally { window.removeEventListener('submit', noted, true); }
return JSON.stringify({ok: 1, how: how, fired: !!seen, prevented: !!(seen && seen.defaultPrevented)});""")


def form_script(gen: str, n: int, mine: Sequence[int]) -> str:
    """What a press of control ``n`` would send, as JSON text (_JS_FORM's snapshotOf): its words, what the press does
    (``how``: submit, enter, key or click), its form's method, action and target as its submitter sends it, every named
    field of that form with its value (whole in length and hash) and whether the owner can see it, and the values of
    the elements Jarvis set (``mine``, by their MINE keys; ``missing``: keys whose element is gone)."""
    return _script("form", {"gen": gen, "n": n, "mine": list(mine)}, """
var el = refOf(ARGS.n);
if (!el) { return stale(); }
return JSON.stringify(snapshotOf(el, ARGS.mine));""")


def scroll_script(gen: str, where: str = "", n: int = 0) -> str:
    """The window scrolls up or down most of a screen, to the top, or to control ``n``."""
    return _script("scroll", {"gen": gen, "where": where, "n": n}, """
var box = got(DOC, 'scrollingElement', document) || got(DOC, 'documentElement', document);
if (ARGS.n) {
  var el = refOf(ARGS.n);
  if (!el) { return stale(); }
  ELEMENT.scrollIntoView.call(el, {block: 'center'});
} else if (ARGS.where === 'top') { window.scrollTo(0, 0); }
else { window.scrollBy(0, Math.round(window.innerHeight * 0.8) * (ARGS.where === 'up' ? -1 : 1)); }
return JSON.stringify({top: window.scrollY, height: box.scrollHeight});""")


# --------------------------------------------------------------------------
# What came back
# --------------------------------------------------------------------------

def _text(value: Any, cap: int = 0) -> str:
    text = value if isinstance(value, str) else ""
    return text[:cap] if cap else text


def _json(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or raw == NO_PAGE:
        return {}
    try:
        found = json.loads(raw)
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


@dataclass(frozen=True)
class Form:
    number: int
    method: str
    action: str = field(repr=False)

    @property
    def host(self) -> str:
        return link_host(self.action) if self.action.casefold().startswith(("https://", "http://")) else ""


@dataclass(frozen=True)
class Control:
    """One control of a page as listed: never in a repr but for its ref and role."""

    n: int
    role: str
    kind: str = ""
    name: str = field(default="", repr=False)
    sr: bool = False                  # its name is one only a screen reader is given (aria-label, title)
    hints: str = field(default="", repr=False)   # its name, id, placeholder ... words: the secret check reads them
    autocomplete: str = field(default="", repr=False)
    required: bool = False
    value: str = field(default="", repr=False)
    options: tuple[str, ...] = field(default=(), repr=False)
    checked: bool = False
    form: int = 0
    href: str = field(default="", repr=False)
    blank: bool = False               # a link that opens a new window: it opens in a new tab

    @property
    def ref(self) -> str:
        return f"e{self.n}"


@dataclass(frozen=True)
class Controls:
    gen: str
    url: str = field(repr=False)
    title: str = field(repr=False)
    controls: tuple[Control, ...] = field(default=(), repr=False)
    forms: tuple[Form, ...] = field(default=(), repr=False)
    left_out: int = 0                 # controls the owner can't see: never listed
    more: bool = False                # there were more than MAX_CONTROLS
    above: int = 0                    # controls above where the page stands: listed after a scroll up

    def get(self, n: int) -> Control | None:
        """The control whose ref is e<n>: the n-th element the controls script kept, as it kept them."""
        return next((control for control in self.controls if control.n == n), None) if n > 0 else None

    def form(self, number: int) -> Form | None:
        return self.forms[number - 1] if 1 <= number <= len(self.forms) else None


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def read_controls(raw: Any, gen: str) -> Controls | None:
    """controls_script's answer for ``gen``, checked and capped; None when it is not one. A control's ref is its place
    in the script's list (the element it kept there), so a row that is no control leaves a gap, never a shift."""
    found = _json(raw)
    if found.get("gen") != gen or not isinstance(found.get("controls"), list):
        return None
    forms = []
    for number, item in enumerate(found.get("forms") if isinstance(found.get("forms"), list) else (), 1):
        method, action = (item if isinstance(item, list) and len(item) == 2 else ("", ""))
        forms.append(Form(number, web.one_line(_text(method), 10).casefold() or "get", _text(action, web.URL_CAP * 2)))
    controls = []
    for place, item in enumerate(found["controls"][:MAX_CONTROLS], 1):
        if not isinstance(item, dict) or item.get("role") not in ROLES:
            continue
        role = item["role"]
        href = _text(item.get("href"), web.URL_CAP + 1) if role == R_LINK else ""
        form = item.get("form") if isinstance(item.get("form"), int) and not isinstance(item.get("form"), bool) else 0
        options = tuple(web.one_line(_text(option), OPTION_CAP) for option in item.get("options", ())[:MAX_OPTIONS]
                        if isinstance(option, str)) if isinstance(item.get("options"), list) else ()
        controls.append(Control(place, role, web.one_line(_text(item.get("kind")), 20).casefold(),
                                web.one_line(_text(item.get("name")), NAME_CAP), item.get("sr") in (1, True),
                                _text(item.get("hints"), HINT_CAP), web.one_line(_text(item.get("auto")), 80).casefold(),
                                item.get("required") is True, web.one_line(_text(item.get("value")), VALUE_CAP),
                                options, item.get("checked") is True, form if 0 <= form <= len(forms) else 0,
                                "" if web_link_problem(href) else href, item.get("blank") is True))
    return Controls(gen, _text(found.get("url"), web.URL_CAP * 2), web.one_line(_text(found.get("title")), web.TITLE_CAP),
                    tuple(controls), tuple(forms), _count(found.get("left")),
                    found.get("more") is True or len(found["controls"]) > MAX_CONTROLS, _count(found.get("above")))


def _quoted(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


def listing(controls: Controls) -> str:
    """The controls as the model reads them: one line each, never raw HTML. Names are the page's words."""
    rows = []
    for control in controls.controls:
        parts = [control.ref, control.role + (f" ({control.kind})" if control.kind and control.kind != "text" else "")]
        parts.append(_quoted(control.name) + (" (a screen-reader name only)" if control.sr else "")
                     if control.name else "(no name)")
        if control.required:
            parts.append("required")
        if control.role in TYPED_ROLES:
            parts.append(f"value {_quoted(control.value)}")
        elif control.role == R_SELECT:
            parts.append(f"chosen {_quoted(control.value)}; options: " + ", ".join(_quoted(o) for o in control.options))
        elif control.role in (R_CHECKBOX, R_RADIO):
            parts.append("checked" if control.checked else "not checked")
        elif control.role == R_LINK:
            parts.append(control.href or "(no public address)")
            if control.blank:
                parts.append("opens a new tab")
        form = controls.form(control.form)
        if form is not None:
            parts.append(f"[form {form.number}: {form.method} to {form.host or 'this page'}]")
        rows.append(" ".join(parts))
    return "\n".join(rows) if rows else "(no controls the owner can see)"


def parse_ref(ref: Any) -> int:
    """"e12" as 12; 0 when it is no ref."""
    found = _REF_RE.fullmatch(ref.strip()) if isinstance(ref, str) else None
    return int(found.group(1)) if found else 0


# --------------------------------------------------------------------------
# What is never typed
# --------------------------------------------------------------------------

def _path_of(address: str) -> str:
    try:
        return urlsplit(address).path or ""
    except ValueError:
        return address


def payment(address: str, title: str) -> bool:
    """A page whose path or title reads like payment, a purchase or a transfer (guard.payment_page)."""
    return guard.payment_page(_path_of(address or "")) or guard.payment_page(title or "")


def field_refused(control: Control, address: str, title: str) -> tuple[str, str] | None:
    """(code, Jarvis's words) when nothing may be typed into or chosen in ``control`` on this page, else None."""
    if control.role == R_FILE:
        return D_FILE_FIELD, FILE_FIELD_TEXT
    if payment(address, title):
        return D_PAYMENT, PAYMENT_TEXT
    tokens = control.autocomplete.split()
    if control.kind == "password" or any(_SECRET_AUTOCOMPLETE_RE.match(token) for token in tokens):
        return D_SECRET_FIELD, SECRET_FIELD_TEXT
    if any(guard.sensitive_label(words) for words in (control.name, control.hints)):
        return D_SECRET_FIELD, SECRET_FIELD_TEXT
    return None


def _luhn(digits: str) -> bool:
    total = 0
    for place, char in enumerate(reversed(digits)):
        value = int(char)
        if place % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def value_refused(text: str) -> tuple[str, str] | None:
    """(code, Jarvis's words) when ``text`` holds what is never typed into a page: what guard.scrub_secrets takes out, a
    Luhn-valid number of 13 to 19 digits (spaces and dashes aside), an IBAN's shape, an ID number's shape."""
    plain = unicodedata.normalize("NFKC", drop_invisible(text or ""))
    if guard.holds_secret(plain):
        return D_SECRET_VALUE, SECRET_VALUE_TEXT
    for found in _DIGIT_RUN_RE.findall(plain):
        digits = re.sub(r"\D", "", found)
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return D_CARD_NUMBER, CARD_NUMBER_TEXT
    if _IBAN_RE.search(plain.upper()):
        return D_IBAN, IBAN_TEXT
    if any(pattern.search(plain.upper()) for pattern in _ID_RES):
        return D_NATIONAL_ID, NATIONAL_ID_TEXT
    return None


def commits(words: str) -> bool:
    """A button whose words commit: buy, pay, sign in, post, delete, confirm, subscribe ..."""
    return guard.commit_button(words) or guard._says(words, _MORE_COMMIT)


def _spelled(text: str) -> str:
    """What no one can see written out ("<U+202E>"), so a card can't hide what it sends."""
    return "".join(f"<U+{ord(char):04X}>" if invisible_char(char) or unicodedata.category(char) in
                   ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") and char not in "\n\t" else char for char in text)


# --------------------------------------------------------------------------
# The Send card
# --------------------------------------------------------------------------

# A card line's kinds (CardLine.kind).
L_PRESS, L_VALUE, L_DEST, L_PAGE_SET, L_NOTE = "press", "value", "dest", "page_set", "note"
L_PAGE_VALUE = "page_value"       # one value the page set itself, in full
SEND_NOTE = "Only a click sends. The site may pass on what it gets."
NO_FORM_DEST = "The page sends it where it chooses; Jarvis can't see where."


@dataclass(frozen=True)
class SendRow:
    label: str = field(repr=False)
    value: str = field(repr=False)
    tainted: bool = False             # the run's taint set hits it: from the owner's mail, files or calendar
    box: bool = False                 # a box or a list Jarvis chose, not words typed


@dataclass(frozen=True)
class PageValue:
    """A value the page set itself in the form that goes: its field's name, the value whole, and why it needs a look."""

    name: str = field(repr=False)
    value: str = field(repr=False)
    hidden: bool = False              # the owner can't see it on the page
    copy: bool = False                # it holds what Jarvis typed into this page (now or before)
    tainted: bool = False             # the run's taint set hits it


@dataclass(frozen=True)
class CardLine:
    """One line of the Send card: ``text`` (a row's label), ``value`` exactly, ``tag`` and ``amber`` when it needs the
    owner's eye."""

    kind: str
    text: str = field(repr=False)
    value: str = field(default="", repr=False)
    tag: str = ""
    amber: bool = False


@dataclass(frozen=True)
class SendOutcome:
    """What came of an approved Send (agent.tools.submit_message words it for the model)."""

    code: str
    title: str = field(default="", repr=False)
    address: str = field(default="", repr=False)
    text: str = field(default="", repr=False)
    hidden: bool = False
    number: int = 0                   # the tab's number, when it still holds what was typed (S_NOT_SENT, S_STAYED)


@dataclass(eq=False)
class SendPlan:
    """One Send card. ``decide(allowed, report, cancelled)``: the owner's click (True) or a Don't, a timeout or a Stop
    (False); never blocks (DriveSession carries it out on its own thread); ``report(SendOutcome)`` hears what came of a
    Send."""

    id: str
    tab: int
    number: int                       # the tab's number as the model knows it
    ref: int
    gen: str
    host: str = field(repr=False)
    words: str = field(repr=False)
    rows: tuple[SendRow, ...] = field(repr=False)
    goes_to: str = field(default="", repr=False)   # the form's action host ("": the press sends no form: a script may)
    insecure: bool = False            # the form's action is not https
    page_set: int = 0                 # values the page set itself in that form
    copies: int = 0                   # ... of which this many hold what Jarvis typed
    page_tainted: int = 0             # ... and this many the run's taint set hits
    snapshot: str = field(default="", repr=False)  # what is compared at the click
    signature: str = field(default="", repr=False)
    decide: Callable[[bool, Callable[[SendOutcome], None] | None, Callable[[], bool]], None] = field(
        default=lambda _allowed, _report, _cancelled: None, repr=False)
    page_values: tuple[PageValue, ...] = field(default=(), repr=False)
    keys: tuple[int, ...] = ()        # the MINE keys of what Jarvis set, as form_script read them
    hosts: tuple[str, ...] = field(default=(), repr=False)   # the only hosts the press may reach (agent.browser)

    def __repr__(self) -> str:        # counts only
        return f"SendPlan({self.id}, tab {self.number}, {len(self.rows)} value(s), {self.page_set} set by the page)"

    @property
    def elsewhere(self) -> bool:
        return not self.goes_to or self.insecure or self.goes_to != self.host

    def kicker(self) -> str:
        return f"SEND ON {self.host}?"

    def question(self) -> str:
        return f"Jarvis wants to press '{_spelled(self.words) or 'the button'}' on {self.host}"

    def card_lines(self) -> tuple[CardLine, ...]:
        lines = [CardLine(L_PRESS, self.question())]
        for row in self.rows:
            consent = row.box and guard.consent_box(row.label) and bool(row.value)
            lines.append(CardLine(L_VALUE, row.label or "(a field with no name)", _spelled(row.value),
                                  TAINT_TAG if row.tainted else CONSENT_TAG if consent else "", row.tainted or consent))
        if self.goes_to:
            lines.append(CardLine(L_DEST, f"Goes to: {self.goes_to}" + (" (not encrypted)" if self.insecure else ""),
                                  amber=self.elsewhere))
        else:
            lines.append(CardLine(L_DEST, NO_FORM_DEST, amber=True))
        if self.page_set:
            more = [f"{self.copies} {COPY_TAG}"] if self.copies else []
            more += [f"{self.page_tainted} {TAINT_TAG}"] if self.page_tainted else []
            lines.append(CardLine(L_PAGE_SET, f"Plus {self.page_set} value{'s' if self.page_set != 1 else ''} the page "
                                  "set itself" + (f" ({'; '.join(more)})" if more else ""),
                                  amber=bool(self.copies or self.page_tainted)))
            for item in self.page_values:
                tag = TAINT_TAG if item.tainted else COPY_TAG if item.copy else HIDDEN_TAG if item.hidden else ""
                lines.append(CardLine(L_PAGE_VALUE, item.name or "(a field with no name)", _spelled(item.value), tag,
                                      item.tainted or item.copy))
        lines.append(CardLine(L_NOTE, SEND_NOTE))
        return tuple(lines)


# --------------------------------------------------------------------------
# A task's caps
# --------------------------------------------------------------------------

CAP_PAGES, CAP_ACTIONS, CAP_SENDS = "pages", "actions", "sends"
CAP_WORDS = {CAP_PAGES: "{n} pages opened", CAP_ACTIONS: "{n} page actions", CAP_SENDS: "{n} Send cards"}


class DriveCaps:
    """One task's pages loaded, drive calls and Send cards ([agent] max_pages, max_actions, max_sends); reached() says
    which is used up, extend() is the owner's yes to "Keep going?": as much again from here. Thread-safe."""

    def __init__(self, max_pages: int = DEFAULT_MAX_PAGES, max_actions: int = DEFAULT_MAX_ACTIONS,
                 max_sends: int = DEFAULT_MAX_SENDS, max_tabs: int = MAX_TABS) -> None:
        self._lock = threading.Lock()
        self._step = {CAP_PAGES: max(1, int(max_pages)), CAP_ACTIONS: max(1, int(max_actions)),
                      CAP_SENDS: max(1, int(max_sends))}
        self._limit = dict(self._step)
        self._count = dict.fromkeys(self._step, 0)
        self.max_tabs = max(1, min(MAX_TABS, int(max_tabs)))

    def __repr__(self) -> str:
        return f"DriveCaps({self._count})"

    def _add(self, kind: str) -> None:
        with self._lock:
            self._count[kind] += 1

    def page(self) -> None:
        self._add(CAP_PAGES)

    def action(self) -> None:
        self._add(CAP_ACTIONS)

    def send(self) -> None:
        self._add(CAP_SENDS)

    def count(self, kind: str) -> int:
        with self._lock:
            return self._count[kind]

    def reached(self) -> str:
        with self._lock:
            return next((kind for kind in (CAP_SENDS, CAP_PAGES, CAP_ACTIONS)
                         if self._count[kind] >= self._limit[kind]), "")

    def extend(self) -> None:
        with self._lock:
            self._limit = {kind: self._count[kind] + self._step[kind] for kind in self._step}

    def words(self, kind: str) -> str:
        return CAP_WORDS[kind].replace("{n}", str(self.count(kind)))


# --------------------------------------------------------------------------
# The seam
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ActResult:
    """What an act or an approved press gave: the script's answer (JSON text; NO_PAGE: no page in that tab), whether the
    tab went to another page meanwhile, and where it is now. ``ok`` False: the tab is gone or its page crashed."""

    answer: Any = field(default=None, repr=False)
    navigated: bool = False
    final_url: str = field(default="", repr=False)
    ok: bool = True


class DriveBrowser(Browser, Protocol):
    """agent.web's Browser, with tabs and the seal (agent.browser builds it in part 2). The window starts with tab 1 (its
    page made with the first load, as today); load, script and picture work on the tab shown. Each call blocks its own
    thread, ``timeout_s`` at most, and returns the moment ``cancelled()`` turns true."""

    def new_tab(self, tab: int, *, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        """A fresh, empty tab with that id and its own off-the-record profile, now the one shown. False: no window."""

    def switch_tab(self, tab: int, *, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        """That tab is the one shown now. False: there is no such tab."""

    def close_tab(self, tab: int, *, unseal: bool, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        """That tab's page and profile destroyed; then, with ``unseal``, once its renderer is gone, the seal lifts."""

    def act(self, tab: int, source: str, *, settle_s: float, timeout_s: float,
            cancelled: Callable[[], bool]) -> ActResult:
        """``source`` run in that tab's isolated world; then a navigation it started is waited for, or ``settle_s``."""

    def seal(self, cut: bool, *, timeout_s: float, cancelled: Callable[[], bool]) -> bool:
        """The window's gate refuses every new connection and, with ``cut``, ends every one open (snapshot_gate). True
        once that holds."""

    def press_approved(self, tab: int, source: str, hosts: Sequence[str], *, settle_s: float, timeout_s: float,
                       cancelled: Callable[[], bool]) -> ActResult:
        """Only after the owner's click on the Send card: the seal lifts for ``hosts`` alone (it holds for every other),
        ``source`` (press_script) runs in that tab, and its navigation is waited for, or ``settle_s``. A new page
        arrived: the seal lifts; none: the window is sealed again as before, its connections cut."""


# --------------------------------------------------------------------------
# One run's use of the window, with tabs and the seal
# --------------------------------------------------------------------------

class _Tab:
    """One tab: its page as read, its controls, what Jarvis put into it and the Send card it waits on."""

    __slots__ = ("id", "page", "results_shown", "book", "held", "typed", "history", "last_key", "pending")

    def __init__(self, tab_id: int) -> None:
        self.id = tab_id
        self.page: web._Page | None = None
        self.results_shown = False
        self.book: Controls | None = None
        self.held = False                 # it holds owner values: the window is sealed
        self.typed: dict[int, tuple[str, bool]] = {}   # MINE key -> (its name, a box or a list)
        self.history: list[str] = []      # every value typed into this page (a page may have kept any of them)
        self.last_key = 0
        self.pending: SendPlan | None = None

    def __repr__(self) -> str:
        return f"_Tab({self.id}, held {self.held}, {len(self.typed)} value(s))"

    def address(self) -> str:
        return self.page.address if self.page is not None else ""

    def title(self) -> str:
        return self.page.title if self.page is not None else ""

    def forget(self) -> None:
        """What was typed went (sent, or the page went with it)."""
        self.held, self.book, self.pending = False, None, None
        self.typed.clear()
        self.history.clear()


@dataclass(frozen=True)
class Typed:
    number: int                       # the tab's number
    label: str = field(repr=False)
    chars: int = 0
    value: str = field(default="", repr=False)    # what the field holds now (choose)


@dataclass(frozen=True)
class Clicked:
    """A click: a link opened (``opened``), a Send card's plan (``plan``), or a button pressed in place."""

    opened: Opened | None = field(default=None, repr=False)
    plan: SendPlan | None = None
    navigated: bool = False
    label: str = field(default="", repr=False)


def _host_of(address: str) -> str:
    return link_host(address) if address and not web_link_problem(address) else ""


def _signed_in_on(before: str, final: str) -> str:
    """A tab whose page was signed in stays so after a click or a Send in it (its profile is the page's for life, and
    its main frame never leaves the listed sites): the host now, "" for a tab that was not."""
    return (site_signins.page_host(final) or before) if before else ""


def _folded(text: str) -> str:
    return " ".join((text or "").split()).casefold()


DECLINE_CLOSE_S = 120.0           # a declined page's close is tried again this long while the window is busy
CLOSING_TEXT = "That tab is being closed after the owner's no. Call this again in a moment."


class DriveSession(WebSession):
    """See the module docs. Tabs are numbered for the model 1, 2, 3 in the order they opened; their ids are this
    session's own (the first is 1, as the window's first page). Thread-safe, one call at a time, as WebSession is."""

    def __init__(self, browser: DriveBrowser | None, engine: str = "duckduckgo", caps: DriveCaps | None = None) -> None:
        self._tabs: list[_Tab] = [_Tab(1)]
        self._active: _Tab | None = self._tabs[0]
        self._next_id = 2
        self._blank = _Tab(0)
        super().__init__(browser, engine)
        self.caps = caps if caps is not None else DriveCaps()
        self._declined: set[str] = set()
        self._typed_values: list[str] = []
        self._links_judged = False        # the firewall judges each click's link first (link_address) ...
        self._judged_link: str | None = None   # ... and this is the address it judged last, for one click

    def __repr__(self) -> str:
        return f"DriveSession({self.book!r}, {len(self._tabs)} tab(s))"

    # WebSession reads and writes the page in the window: here, the tab shown.
    @property
    def _page(self) -> web._Page | None:  # type: ignore[override]
        return (self._active or self._blank).page

    @_page.setter
    def _page(self, page: web._Page | None) -> None:
        tab = self._active or self._blank
        tab.page = page
        if page is None:
            tab.book = None

    @property
    def _results_shown(self) -> bool:  # type: ignore[override]
        return (self._active or self._blank).results_shown

    @_results_shown.setter
    def _results_shown(self, shown: bool) -> None:
        (self._active or self._blank).results_shown = shown

    # ---- what the others ask (any thread; never waits for the window) ------------------------------------

    def tab_count(self) -> int:
        return len(self._tabs)

    def tab_shown(self) -> int:
        return self._number(self._active) if self._active is not None else 0

    def holding(self) -> int:
        """The number of the tab that holds owner values, 0 when none does."""
        tab = self._held_tab()
        return self._number(tab) if tab is not None else 0

    def link_address(self, ref: Any) -> str:
        """The address the link ``ref`` of the tab shown opens, "" when it is no link of this page (the firewall's). It
        is kept: the click that follows opens it only if its link is still this address."""
        control = self._lookup(ref)
        address = control.href if control is not None and control.role == R_LINK else ""
        self._links_judged, self._judged_link = True, address
        return address

    def label_of(self, ref: Any) -> str:
        control = self._lookup(ref)
        return control.name if control is not None else ""

    def host_shown(self) -> str:
        tab = self._active
        return _host_of(tab.address()) if tab is not None else ""

    def typed_in(self, line: str) -> bool:
        """``line`` holds a value Jarvis typed into a page in this run (a spoken line never does)."""
        folded = _folded(line)
        return any(len(value) >= TYPED_SEEN_MIN and value in folded for value in self._typed_values)

    def _lookup(self, ref: Any) -> Control | None:
        tab = self._active
        book = tab.book if tab is not None else None
        return book.get(parse_ref(ref)) if book is not None else None

    def _held_tab(self) -> _Tab | None:
        return next((tab for tab in self._tabs if tab.held), None)

    def _number(self, tab: _Tab) -> int:
        return self._tabs.index(tab) + 1 if tab in self._tabs else 0

    # ---- checks -------------------------------------------------------------------------------------------

    def _no_loads(self) -> None:
        held = self._held_tab()
        if held is not None:
            number = self._number(held)
            raise WebRefused(D_SEALED, SEALED_TEXT.format(tab=number))

    def _free(self, tab: _Tab | None) -> None:
        """Nothing acts on a tab whose Send card waits (or whose page goes after the owner's no)."""
        plan = tab.pending if tab is not None else None
        if plan is not None:
            raise WebRefused(D_CARD_UP, CLOSING_TEXT if plan.signature in self._declined else CARD_UP_TEXT)

    def _tab(self, number: Any) -> _Tab:
        if number is None:
            if self._active is None:
                raise WebRefused(D_NO_TAB, NO_TAB_TEXT)
            return self._active
        if isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= len(self._tabs):
            return self._tabs[number - 1]
        raise WebRefused(D_NO_TAB, NO_TAB_TEXT)

    def _control(self, tab: _Tab, ref: Any) -> Control:
        self._free(tab)
        if tab.book is None:
            raise WebRefused(D_NO_REF, NO_LIST_TEXT)
        control = tab.book.get(parse_ref(ref))
        if control is None:
            raise WebRefused(D_NO_REF, NO_REF_TEXT)
        return control

    def _may_hold(self, tab: _Tab) -> None:
        other = self._held_tab()
        if other is not None and other is not tab:
            raise WebRefused(D_OTHER_TAB, OTHER_TAB_TEXT.format(tab=self._number(other)))

    def _live(self, tab: _Tab, address: str, title: str) -> str:
        """The page as it is now (``address``, ``title``: what a script just read in it) is judged, not only the one
        loaded: a payment page is refused, and so is a page no longer on the site the tab loaded. Its host."""
        if payment(address, title) or payment(tab.address(), tab.title()):
            raise WebRefused(D_PAYMENT, PAYMENT_TEXT)
        host = _host_of(address)
        if address.startswith(LOCAL_BASE_URL):     # local HTML (the html_for seam: tests only) stands for its address
            host = _host_of(tab.address())
        if host != _host_of(tab.address()):
            raise WebRefused(D_MOVED, MOVED_TEXT.format(tab=self._number(tab)))
        return host

    @contextmanager
    def _turn_for(self, call: Call) -> Iterator[DriveBrowser]:
        with self._one_at_a_time(call) as browser:
            yield browser  # type: ignore[misc]

    @staticmethod
    def _wait(call: Call, cap: float) -> float:
        return min(cap, max(0.1, call.left()))

    def _act_raw(self, browser: DriveBrowser, tab: _Tab, source: str, call: Call,
                 settle_s: float = 0.0) -> tuple[dict[str, Any], str]:
        """``source`` in ``tab``; its answer as a dict, and as the text it came as. A ref the page no longer has:
        STALE; no page: NO_PAGE."""
        done = browser.act(tab.id, source, settle_s=settle_s, timeout_s=self._wait(call, web.SCRIPT_TIMEOUT_S + settle_s),
                           cancelled=call.cancelled)
        call.check()
        if not done.ok or done.answer == NO_PAGE:
            tab.book = None
            raise WebRefused(web.W_NO_PAGE, web.NO_PAGE_TEXT)
        if done.navigated:
            tab.book = None
        answer = _json(done.answer)
        if not answer:
            raise WebRefused(web.W_NO_ANSWER, web.NO_ANSWER_TEXT)
        if answer.get("stale"):
            tab.book = None
            raise WebRefused(D_STALE, STALE_TEXT)
        return answer, done.answer

    def _act(self, browser: DriveBrowser, tab: _Tab, source: str, call: Call, settle_s: float = 0.0) -> dict[str, Any]:
        return self._act_raw(browser, tab, source, call, settle_s)[0]

    def _seal(self, browser: DriveBrowser, tab: _Tab, call: Call) -> None:
        """The first value into ``tab``: the window is sealed, its open connections cut, before anything is typed."""
        if tab.held:
            return
        if not browser.seal(True, timeout_s=self._wait(call, web.SCRIPT_TIMEOUT_S), cancelled=call.cancelled):
            call.check()
            raise WebRefused(D_NO_SEAL, NO_SEAL_TEXT)
        tab.held = True

    def _record(self, tab: _Tab, label: str, box: bool, text: str) -> int:
        """What Jarvis is about to set is kept BEFORE the script runs (one that ran though its answer never came is on
        the card all the same, or the card is refused): its MINE key."""
        tab.last_key += 1
        tab.typed[tab.last_key] = (label, box)
        if text:
            tab.history.append(text)
            if _folded(text):
                self._typed_values.append(_folded(text))
        return tab.last_key

    def _unrecord(self, tab: _Tab, key: int, text: str) -> None:
        """The script answered that it set nothing."""
        tab.typed.pop(key, None)
        if text and text in tab.history:
            tab.history.reverse()
            tab.history.remove(text)
            tab.history.reverse()
            folded = _folded(text)
            if folded in self._typed_values:
                del self._typed_values[len(self._typed_values) - 1 - self._typed_values[::-1].index(folded)]

    # ---- loads (web_search, open_page, show_file, a link) -------------------------------------------------

    def search(self, query: str, call: Call) -> web.Found:
        with self._turn_for(call) as browser:
            self._no_loads()
            self._free(self._active)
            self.caps.page()
            return self._search_in(browser, query, call)

    def open_page(self, address: str, call: Call, *, new_tab: bool = False) -> Opened:
        why = web_link_problem(address)
        if why:
            raise WebRefused(web.W_NOT_PUBLIC, web.NOT_PUBLIC_TEXT.format(why=why))
        with self._turn_for(call) as browser:
            return self._load(browser, address, call, new_tab)

    def _load(self, browser: DriveBrowser, address: str, call: Call, new_tab: bool) -> Opened:
        self._no_loads()
        if new_tab:
            self._open_tab(browser, call)
        else:
            self._free(self._active)
        self.caps.page()
        return self._open_in(browser, address, call)

    def show(self, folder: Any, name: str, call: Call) -> Opened:
        with self._turn_for(call) as browser:
            self._no_loads()
            self._free(self._active)
            return self._show_in(browser, folder, name, call)

    # ---- tabs ---------------------------------------------------------------------------------------------

    def _open_tab(self, browser: DriveBrowser, call: Call) -> _Tab:
        if len(self._tabs) >= self.caps.max_tabs:
            raise WebRefused(D_TABS, TABS_TEXT)
        tab = _Tab(self._next_id)
        self._next_id += 1
        if not browser.new_tab(tab.id, timeout_s=self._wait(call, web.SCRIPT_TIMEOUT_S), cancelled=call.cancelled):
            call.check()
            raise WebRefused(web.W_NO_WINDOW, web.NO_WINDOW_TEXT)
        self._tabs.append(tab)
        self._active = tab
        return tab

    def switch_tab(self, number: int, call: Call) -> int:
        with self._turn_for(call) as browser:
            tab = self._tab(number)
            if not browser.switch_tab(tab.id, timeout_s=self._wait(call, web.SCRIPT_TIMEOUT_S),
                                      cancelled=call.cancelled):
                call.check()
                raise WebRefused(D_NO_TAB, NO_TAB_TEXT)
            self._active = tab
            self.caps.action()
            return self._number(tab)

    def close_tab(self, number: int, call: Call) -> int:
        """That tab's page goes, with whatever Jarvis typed into it (the seal lifts only once it is gone). The last tab
        is followed by a fresh empty one. How many tabs are left. A tab that did not go stays as it was, sealed."""
        with self._turn_for(call) as browser:
            tab = self._tab(number)
            self._free(tab)
            if not self._close(browser, tab, call):
                call.check()
                raise WebRefused(D_NOT_CLOSED, NOT_CLOSED_TEXT.format(tab=self._number(tab)))
            self.caps.action()
            return len(self._tabs)

    def _close(self, browser: DriveBrowser, tab: _Tab, call: Call) -> bool:
        """True once the tab's page went (and, when it held values, the seal lifted). False: it stays as it was, held
        and sealed if it was."""
        held = tab.held
        if not browser.close_tab(tab.id, unseal=held, timeout_s=self._wait(call, DESTROY_S), cancelled=call.cancelled):
            return False
        tab.forget()
        if tab in self._tabs:
            self._tabs.remove(tab)
        if self._active is tab:
            self._active = self._tabs[-1] if self._tabs else None
        if not self._tabs:
            fresh = _Tab(self._next_id)
            self._next_id += 1
            if browser.new_tab(fresh.id, timeout_s=self._wait(call, web.SCRIPT_TIMEOUT_S), cancelled=call.cancelled):
                self._tabs.append(fresh)
                self._active = fresh
        elif self._active is not None:
            browser.switch_tab(self._active.id, timeout_s=self._wait(call, web.SCRIPT_TIMEOUT_S),
                               cancelled=call.cancelled)
        return True

    # ---- the controls -------------------------------------------------------------------------------------

    def list_controls(self, call: Call, number: int | None = None) -> tuple[int, Controls]:
        """(the tab's number, its controls as they are now). Refs from an earlier list are gone; what Jarvis set stays
        his, by its own key (a new list never renumbers it)."""
        with self._turn_for(call) as browser:
            tab = self._tab(number)
            self._free(tab)
            gen = secrets.token_hex(6)
            answer = self._act(browser, tab, controls_script(gen), call)
            found = read_controls(json.dumps(answer), gen)
            if found is None:
                raise WebRefused(web.W_NO_ANSWER, web.NO_ANSWER_TEXT)
            tab.book = found
            self.caps.action()
            return self._number(tab), found

    def type_text(self, ref: Any, text: str, call: Call) -> Typed:
        """``text`` typed into a field of the tab shown, in place of what it held; the window is sealed first. A field
        takes one value a page: typing into it again is refused."""
        refused = value_refused(text)
        if refused:
            raise WebRefused(*refused)
        with self._turn_for(call) as browser:
            tab = self._tab(None)
            control = self._control(tab, ref)
            if control.role not in TYPED_ROLES:
                raise WebRefused(D_WRONG_KIND, WRONG_KIND_TEXT["type"])
            book = tab.book
            assert book is not None
            self._may_hold(tab)
            self._live(tab, book.url, book.title)
            refused = field_refused(control, book.url, book.title)
            if refused:
                raise WebRefused(*refused)
            self._seal(browser, tab, call)
            _per, _step, total = _typing(text)
            key = self._record(tab, control.name, False, text)
            try:
                answer = self._act(browser, tab, type_script(book.gen, control.n, text, key), call,
                                   settle_s=total / 1000 + 0.3)
            except WebRefused as refused_now:
                if refused_now.code == D_STALE:          # the script found no such element: nothing was typed
                    self._unrecord(tab, key, text)
                raise
            if answer.get("again"):
                self._unrecord(tab, key, text)
                raise WebRefused(D_TYPED_ONCE, TYPED_ONCE_TEXT.format(tab=self._number(tab)))
            self.caps.action()
            return Typed(self._number(tab), control.name, len(text))

    def choose(self, ref: Any, call: Call, *, option: str | None = None, checked: bool | None = None) -> Typed:
        """A list's option or a box of the tab shown; the window is sealed first."""
        if (option is None) == (checked is None):
            raise WebRefused(D_BAD_INPUT, CHOOSE_INPUT_TEXT)
        with self._turn_for(call) as browser:
            tab = self._tab(None)
            control = self._control(tab, ref)
            if control.role not in CHOSEN_ROLES:
                raise WebRefused(D_WRONG_KIND, WRONG_KIND_TEXT["choose"])
            if (control.role == R_SELECT) != (option is not None):
                raise WebRefused(D_BAD_INPUT, CHOOSE_INPUT_TEXT)
            if control.role == R_SELECT and not any(option.strip().casefold() == known.casefold()
                                                    for known in control.options):
                raise WebRefused(D_NO_OPTION, NO_OPTION_TEXT)
            book = tab.book
            assert book is not None
            self._may_hold(tab)
            self._live(tab, book.url, book.title)
            refused = field_refused(control, book.url, book.title)
            if refused:
                raise WebRefused(*refused)
            self._seal(browser, tab, call)
            key = self._record(tab, control.name, True, "")
            try:
                answer = self._act(browser, tab, choose_script(book.gen, control.n, option, checked, key), call)
            except WebRefused as refused_now:
                if refused_now.code == D_STALE:
                    self._unrecord(tab, key, "")
                raise
            if answer.get("missing"):
                self._unrecord(tab, key, "")
                raise WebRefused(D_NO_OPTION, NO_OPTION_TEXT)
            kept = answer.get("key")
            if isinstance(kept, int) and not isinstance(kept, bool) and kept != key:   # chosen before: its key holds
                self._unrecord(tab, key, "")
                tab.typed.setdefault(kept, (control.name, True))
            self.caps.action()
            return Typed(self._number(tab), control.name, 0, web.one_line(_text(answer.get("value")), VALUE_CAP))

    def scroll(self, call: Call, *, where: str = "", ref: Any = None) -> tuple[int, int]:
        """(where the tab shown stands, its height), after a scroll up, down, to the top or to a control."""
        with self._turn_for(call) as browser:
            tab = self._tab(None)
            self._free(tab)
            n = 0
            if ref is not None:
                n = self._control(tab, ref).n
            gen = tab.book.gen if tab.book is not None else ""
            answer = self._act(browser, tab, scroll_script(gen, where, n), call)
            self.caps.action()
            top, height = answer.get("top"), answer.get("height")
            return (top if isinstance(top, int) else 0), (height if isinstance(height, int) else 0)

    # ---- clicks and presses: free, or the Send card -------------------------------------------------------

    def click(self, ref: Any, call: Call, taint_hits: Callable[[str], Sequence[str]] = lambda _text: ()) -> Clicked:
        """A link opens as open_page would (in a new tab when it opens a new window), refused while a tab holds owner
        values, and only when it is still the address the firewall judged. A submit, a button whose words commit, or
        any button of a tab that holds owner values or is signed in as the owner gives the plan of a Send card. Any
        other button is clicked in place. Signed in, a link whose words would change the account is refused."""
        judged, self._judged_link = self._judged_link, None
        with self._turn_for(call) as browser:
            tab = self._tab(None)
            control = self._control(tab, ref)
            signed_in = tab.page.signed_in if tab.page is not None else ""
            if control.role == R_LINK:
                if not control.href:
                    raise WebRefused(D_NO_LINK, NO_LINK_TEXT)
                if self._links_judged and judged != control.href:   # the tab or the page changed since the check
                    raise WebRefused(D_STALE, STALE_TEXT)
                if signed_in and (commits(control.name) or site_signins.signs_out(control.name)):
                    raise WebRefused(D_SIGNED_IN, SIGNED_IN_LINK_TEXT)   # read only, signed in as the owner
                opened = self._load(browser, control.href, call, control.blank)
                return Clicked(opened=opened, label=control.name)
            if control.role not in (R_BUTTON, R_SUBMIT):
                raise WebRefused(D_WRONG_KIND, WRONG_KIND_TEXT["click"])
            # Signed in as the owner the page is read only: every button goes to the owner's Send card.
            if control.role == R_SUBMIT or tab.held or commits(control.name) or signed_in:
                return Clicked(plan=self._plan(browser, tab, control, call, taint_hits), label=control.name)
            gen = tab.book.gen if tab.book is not None else ""
            done = browser.act(tab.id, click_script(gen, control.n), settle_s=CLICK_SETTLE_S,
                               timeout_s=self._wait(call, web.SCRIPT_TIMEOUT_S + CLICK_SETTLE_S),
                               cancelled=call.cancelled)
            call.check()
            answer = _json(done.answer) if done.ok else {}
            if answer.get("stale"):
                tab.book = None
                raise WebRefused(D_STALE, STALE_TEXT)
            if not done.ok:
                raise WebRefused(web.W_NO_PAGE, web.NO_PAGE_TEXT)
            self.caps.action()
            if not done.navigated:
                return Clicked(label=control.name)
            tab.book = None
            self.caps.page()
            read = self._read(browser, call)
            final = done.final_url or read.url
            if final and not web_link_problem(final):
                self.book.see(final)
            page = web._Page(read.title, final, read.text, read.length, False, final, read.hidden,
                             _signed_in_on(signed_in, final))
            tab.page = page
            links = []
            for words, link in read.links[:web.MAX_LINKS]:
                number = self.book.number(link)
                if number:
                    links.append((number, words, link))
            return Clicked(opened=self._part(page, tuple(links), self._shot(browser, call)), navigated=True,
                           label=control.name)

    def press(self, ref: Any, call: Call, taint_hits: Callable[[str], Sequence[str]] = lambda _text: ()) -> SendPlan:
        """Enter in a field, or a submit or button pressed: always a Send card's plan."""
        with self._turn_for(call) as browser:
            tab = self._tab(None)
            control = self._control(tab, ref)
            if control.role == R_LINK:
                raise WebRefused(D_WRONG_KIND, WRONG_KIND_TEXT["press"])
            if control.role == R_FILE:
                raise WebRefused(D_FILE_FIELD, FILE_FIELD_TEXT)
            return self._plan(browser, tab, control, call, taint_hits)

    def _snapshot(self, browser: DriveBrowser, tab: _Tab, n: int, call: Call, gen: str,
                  keys: Sequence[int]) -> tuple[dict[str, Any], str]:
        return self._act_raw(browser, tab, form_script(gen, n, list(keys)), call)

    @staticmethod
    def _whole(item: Any) -> tuple[str, int, str]:
        """(the value as given, its whole length, its hash) of a value form_script read."""
        if not isinstance(item, dict):
            return "", -1, ""
        length = item.get("len")
        return (_text(item.get("value")), length if isinstance(length, int) and not isinstance(length, bool) else -1,
                _text(item.get("hash")))

    @classmethod
    def _compared(cls, found: dict[str, Any]) -> str:
        fields = [(item.get("k"), _text(item.get("name")), *cls._whole(item), item.get("hidden") is True)
                  for item in found.get("fields", ()) if isinstance(item, dict)]
        mine = found.get("mine") if isinstance(found.get("mine"), dict) else {}
        missing = found.get("missing") if isinstance(found.get("missing"), list) else ["?"]
        return json.dumps([_text(found.get("words")), found.get("form") is True, _text(found.get("how")),
                           _text(found.get("method")), _text(found.get("action")), _text(found.get("target")),
                           _text(found.get("url")), sorted((str(k), *cls._whole(v)) for k, v in mine.items()), fields,
                           missing], default=str)

    def _plan(self, browser: DriveBrowser, tab: _Tab, control: Control, call: Call,
              taint_hits: Callable[[str], Sequence[str]]) -> SendPlan:
        if payment(tab.address(), tab.title()):
            raise WebRefused(D_PAYMENT, PAYMENT_TEXT)
        self._may_hold(tab)
        gen = tab.book.gen if tab.book is not None else ""
        keys = tuple(tab.typed)
        found, _raw = self._snapshot(browser, tab, control.n, call, gen, keys)
        number = self._number(tab)
        mine = found.get("mine") if isinstance(found.get("mine"), dict) else {}
        missing = found.get("missing")
        if not isinstance(missing, list) or missing or any(str(key) not in mine for key in keys):
            raise WebRefused(D_MISSING, MISSING_TEXT.format(tab=number))
        host = self._live(tab, _text(found.get("url")), _text(found.get("title")))
        if _text(found.get("target")).strip().casefold() not in ("", "_self"):
            raise WebRefused(D_OTHER_WINDOW, OTHER_WINDOW_TEXT)
        too_long = WebRefused(D_TOO_LONG, TOO_LONG_TEXT.format(cap=TYPE_CAP))
        rows, values = [], set()
        for key, (label, box) in tab.typed.items():
            value, length, _hash = self._whole(mine.get(str(key)))
            if not 0 <= length <= TYPE_CAP:
                raise too_long
            values.add(value)
            rows.append(SendRow(label, value, bool(value and taint_hits(value)), box))
        seen = [text for text in tab.history if len(text) >= TYPED_SEEN_MIN]
        page_values = []
        for item in found.get("fields", ()):
            if not isinstance(item, dict) or item.get("k") in tab.typed:
                continue
            value, length, _hash = self._whole(item)
            if not value and length == 0:
                continue
            if not 0 <= length <= TYPE_CAP:
                raise too_long
            copy = value in values or any(text in value or (len(value) >= TYPED_SEEN_MIN and value in text)
                                          for text in seen)
            page_values.append(PageValue(web.one_line(_text(item.get("name")), NAME_CAP), value,
                                         item.get("hidden") is True, copy, not copy and bool(taint_hits(value))))
        if len(page_values) > PAGE_SET_SHOWN:
            raise WebRefused(D_TOO_MANY, TOO_MANY_TEXT.format(cap=PAGE_SET_SHOWN))
        submits = found.get("form") is True and _text(found.get("how")) in ("submit", "enter")
        action = _text(found.get("action")) if submits else ""
        goes_to = link_host(action) if action.casefold().startswith(("https://", "http://")) else ""
        words = web.one_line(_text(found.get("words")), NAME_CAP) or control.name
        # What the owner said no to, never tried again: what Jarvis put in, where and how (not what the page set, which
        # a page could change each time to try again).
        signature = hashlib.sha256(json.dumps([host, goes_to, words, sorted((row.label, row.value) for row in rows)])
                                   .encode("utf-8")).hexdigest()
        if signature in self._declined:
            raise WebRefused(D_DECLINED, DECLINED_TEXT)
        plan = SendPlan(secrets.token_hex(4), tab.id, number, control.n, gen, host or "this page", words, tuple(rows),
                        goes_to, bool(goes_to and not action.casefold().startswith("https://")), len(page_values),
                        sum(item.copy for item in page_values), sum(item.tainted for item in page_values),
                        self._compared(found), signature, page_values=tuple(page_values), keys=keys,
                        hosts=tuple(dict.fromkeys(name for name in (host, goes_to) if name)))
        plan.decide = lambda allowed, report, cancelled, plan=plan: self._decide(plan, allowed, report, cancelled)
        tab.pending = plan
        self.caps.send()
        return plan

    def withdraw(self, plan: SendPlan) -> None:
        """The card could not be shown after all (another wait is up), or what came of it is told: nothing waits on
        it."""
        for tab in self._tabs:
            if tab.pending is plan:
                tab.pending = None

    def _by_id(self, tab_id: int) -> _Tab | None:
        return next((tab for tab in self._tabs if tab.id == tab_id), None)

    def _decide(self, plan: SendPlan, allowed: bool, report: Callable[[SendOutcome], None] | None,
                cancelled: Callable[[], bool]) -> None:
        threading.Thread(target=self._decided, args=(plan, allowed, report, cancelled), name="drive-send",
                         daemon=True).start()

    def _decline(self, plan: SendPlan, deadline_s: float = DECLINE_CLOSE_S) -> bool:
        """The owner's no: the page goes with what was typed into it while the window is still sealed, tried again
        while the window is busy, for ``deadline_s`` at most. True once it went. If it never does, the tab stays held
        and sealed, its card gone, and close_tab is the model's to call."""
        end = time.monotonic() + deadline_s
        while True:
            call = Call(lambda: False, DESTROY_S + web.MARGIN_S + web.MIN_TRY_S)
            try:
                with self._turn_for(call) as browser:
                    tab = self._by_id(plan.tab)
                    if tab is None or tab.pending is not plan:
                        return True
                    if self._close(browser, tab, call):
                        return True
            except WebRefused:
                pass
            except Exception:  # noqa: BLE001 - nothing of it may reach the run
                pass
            if time.monotonic() >= end:
                self.withdraw(plan)
                return False
            time.sleep(0.2)

    def _decided(self, plan: SendPlan, allowed: bool, report: Callable[[SendOutcome], None] | None,
                 cancelled: Callable[[], bool]) -> None:
        """On its own thread. No: the same send is never tried again, and the page goes with what was typed into it
        while the window is still sealed. Send: the values are read again and must be the card's, then the seal lifts
        for the card's hosts alone, the same element is pressed (once more checked in that very task), and what the
        page shows then is reported."""
        if not allowed:
            self._declined.add(plan.signature)
            self._decline(plan)
            return
        outcome = SendOutcome(S_UNKNOWN)
        try:
            outcome = self._send(plan, Call(cancelled, SEND_S + web.MARGIN_S))
        except WebRefused as refused:
            outcome = (SendOutcome(S_NOT_SENT, number=plan.number) if refused.code in (web.W_BUSY, web.W_STOPPED)
                       else SendOutcome(S_GONE if refused.code in (web.W_NO_PAGE, D_STALE) else S_UNKNOWN))
        except Exception:  # noqa: BLE001 - the run hears something either way
            outcome = SendOutcome(S_UNKNOWN)
        finally:
            self.withdraw(plan)
            if report is not None:
                try:
                    report(outcome)
                except Exception:  # noqa: BLE001
                    pass

    def _send(self, plan: SendPlan, call: Call) -> SendOutcome:
        with self._turn_for(call) as browser:
            tab = self._by_id(plan.tab)
            if tab is None or tab.pending is not plan:
                return SendOutcome(S_GONE)
            try:
                again, raw = self._snapshot(browser, tab, plan.ref, call, plan.gen, plan.keys)
            except WebRefused:
                tab.pending = None
                self._close(browser, tab, call)
                return SendOutcome(S_GONE)
            if self._compared(again) != plan.snapshot:
                tab.pending = None
                return SendOutcome(S_CHANGED)
            signed_in = tab.page.signed_in if tab.page is not None else ""
            done = browser.press_approved(tab.id, press_script(plan.gen, plan.ref, plan.keys, raw), plan.hosts,
                                          settle_s=SETTLE_S, timeout_s=self._wait(call, SETTLE_S + web.SCRIPT_TIMEOUT_S),
                                          cancelled=call.cancelled)
            tab.pending = None
            answer = _json(done.answer) if done.ok else {}
            if answer.get("stale"):           # the page changed at the very last: nothing pressed, and it goes
                self._close(browser, tab, call)
                return SendOutcome(S_GONE)
            if answer.get("changed"):         # ... or what it would send did: nothing pressed, still sealed
                return SendOutcome(S_CHANGED)
            if not done.ok or not answer.get("ok"):
                return SendOutcome(S_UNKNOWN, number=self._number(tab))
            read = self._read_tab(browser, tab, call)
            if not done.navigated:            # no new page: the window is sealed again, and the tab still holds it
                code = S_NOT_SENT if answer.get("how") in ("submit", "enter") and not answer.get("fired") else S_STAYED
                if read is None:
                    return SendOutcome(code, number=self._number(tab))
                return SendOutcome(code, read.title, tab.address(), read.text[:AFTER_SEND_CHARS], read.hidden,
                                   self._number(tab))
            tab.forget()
            if read is None:
                return SendOutcome(S_SENT, address=done.final_url)
            final = done.final_url or read.url
            if final and not web_link_problem(final):
                self.book.see(final)
            tab.page = web._Page(read.title, final, read.text, read.length, False, final, read.hidden,
                                 _signed_in_on(signed_in, final))
            return SendOutcome(S_SENT, read.title, final, read.text[:AFTER_SEND_CHARS], read.hidden)

    def _read_tab(self, browser: DriveBrowser, tab: _Tab, call: Call) -> web.PageRead | None:
        try:
            done = browser.act(tab.id, web.read_script(), settle_s=0.0,
                               timeout_s=self._wait(call, web.SCRIPT_TIMEOUT_S), cancelled=call.cancelled)
        except Exception:  # noqa: BLE001 - what was sent went: only the read failed
            return None
        return web.read_page(done.answer) if done.ok else None


def keep_going_words(kind: str, caps: DriveCaps | None) -> str:
    """"{n} pages opened" and the like, for "Keep going?" (agent.runner)."""
    return caps.words(kind) if caps is not None and kind in CAP_WORDS else ""


__all__ = ["ActResult", "CardLine", "Clicked", "Control", "Controls", "DriveBrowser", "DriveCaps", "DriveSession", "Form",
           "PageValue", "SendOutcome", "SendPlan", "SendRow", "Typed", "choose_script", "click_script", "commits", "controls_script",
           "field_refused", "form_script", "listing", "parse_ref", "payment", "press_script", "read_controls",
           "scroll_script", "type_script", "value_refused"]
