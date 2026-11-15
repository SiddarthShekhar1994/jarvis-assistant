/* The board's page (the new look, parts 3, 4, 5a, 5b and 5c): Jarvis's own code, no library. Every element is made with
   createElement, every word is set with textContent and every listener with addEventListener: no markup is ever read,
   no address is ever opened. The app sends board.apply(<JSON text>), checked in Python first and checked here again;
   the page answers on its web channel with bridge.post(kind, <JSON text>), the short list Python accepts (ready, layout,
   focus, open, close, pin, bring_back, retry, go_approvals, clear, restore, show_new, reading, focus_out, landed, big).
   DESIGN-SPEC 3.1 (the bar, the groups, the index strip, "N more below" and the fades), 3.2 (the frame), 3.3 (the kinds
   and the satellite), 3.4 (its states: lit, changed), 3.5 (order, parking, pin, close, Clear, open big), 3.6 (the
   keyboard), 5 (motion) and 8 (the cue line's stub). The app is the source of truth: what the owner does shows here at
   once, the app decides and sends a frame's flags again when its decision differs. */
(function () {
  'use strict';

  var MESSAGE_CAP = 32768;                     // bytes of one message's JSON (board.protocol.MESSAGE_CAP) ...
  var PICTURE_CAP = 419840;                    // ... a picture message's one data: URL apart (PICTURE_CAP)
  var PICTURE_PREFIX = 'data:image/jpeg;base64,';
  var BASE64 = /^[A-Za-z0-9+\/]*={0,2}$/;
  var ID = /^[A-Za-z0-9_-]{1,40}$/;
  var CAPS = {title: 60, meta: 40, text: 400, status_text: 160, status_meta: 40, waits: 60, rows: 8, spans: 32,
              ticks: 13, tick: 8, pill: 16, topic: 40, time: 16};
  var DAY_MINUTES = 1440;
  var FRAME_FIELDS = ['type', 'id', 'kind', 'title', 'meta', 'body', 'status', 'status_text', 'status_meta', 'waits',
                      'changed', 'pinned', 'closed', 'fact', 'group'];
  var MAX_GROUPS = 8;
  var DESK_STATES = ['enter', 'leave'];
  var FOCUS_PARTS = ['board', 'strip', 'next', 'prev'];
  var GROUP_FADE_MS = 160;                     // a group that leaves for room fades, then goes
  var STATUSES = ['ok', 'failed', 'empty'];
  var CALENDAR_STATES = ['', 'past', 'now', 'next', 'free', 'new', 'struck', 'today'];
  var SPAN_KINDS = ['busy', 'next', 'free'];
  var TODO_STATES = ['open', 'waits', 'done'];
  var MOTIONS = {lively: 1, calm: 1.6, off: 0};
  var MAX_FRAMES = 24;
  var MAX_PINS = 6;                            // a 7th pin says why in the frame's meta (board.protocol.MAX_PINS)
  var MAX_ANIMATED = 6;                        // frames of one arrival that draw in; the rest are there at once
  var STAGGER_MS = 80, DRAW_MS = 800, LAP_MS = 900, FADE_MS = 160, HEIGHT_MS = 200;
  var CLOSED_MS = 8000;                        // a closed frame's row offers Bring back this long (the app removes it)
  var NOTE_MS = 4000;                          // the 7th pin's note stays this long
  var ROOM_MS = 220;                           // a group arrives: room is made for it ...
  var ROOM_WAIT_MS = 40;                       // ... once the frames that came with it are in
  var LAYOUT_MS = 100;                         // at most 10 layouts a second ...
  var SCROLL_END_MS = 150;                     // ... and one more this long after a scroll stops
  var VIEW_MS = 16;                            // the strip, "N more below" and the fades follow a change this soon
  var READING_MS = 1000;                       // a sign of reading (a wheel, a key, a press) at most once a second
  var BATCH_MS = 300, FONT_WAIT_MS = 1500;
  // The frames area's own rules (DESIGN-SPEC 2.1, 2.3, 3.1): small kinds pair from a 500 px area and go three across
  // from 756 px (each at least 244 px); a frame is in view between 6 px from its top and 40 px from its end; one whose
  // bottom edge is inside the last 16 px is not fully in view (it counts in "N more below").
  var PAIRS_FROM = 500, TRIOS_FROM = 756, IN_VIEW_TOP = 6, IN_VIEW_END = 40, EDGE = 16;
  var SMALL_KINDS = ['todos', 'timeline', 'chart', 'maths'];
  // Part 5c: a frame lands when its outline is complete (260 ms into its draw-in); the cue line's stub draws in after
  // the app's native part (board.css: 200 ms, then 40 ms) and fades 400 ms after its sentence; it meets the frame in
  // the middle of the part of it in sight (at least 4 px: desk_cue.anchor does the same), its dot 6 px from the frame.
  // Open big scales 260 ms up and 220 ms down.
  var OUTLINE_MS = 260, CUE_FADE_MS = 400, MIN_SEEN = 4, DOT_GAP = 6;
  var SCALE_MS = 260, UNSCALE_MS = 220, BIG_LIMIT = 100000;
  var LANDED_STEPS = ['outline', 'done'];
  var SVG = 'http://www.w3.org/2000/svg';
  var WORDS = {
    changed: 'Changed', tryAgain: 'Try again', bringBack: 'Bring back', closed: 'Closed: ',
    keysFirst: 'Enter open big · P pin', keysSecond: 'Del close · Esc ask bar', dot: '·',
    approvalsKey: 'Ctrl 3', pin: 'Pin', unpin: 'Unpin', openBig: 'Open big', close: 'Close',
    waits: 'Waits', done: 'Done', pictureOnly: 'Picture only', unread: 'Unread',
    board: 'Board', frame: ' frame', frames: ' frames', waitsCount: ' waits', all: 'All', pinned: 'Pinned',
    clear: 'Clear', show: 'Show', newAbove: 'New answer above', moreBelow: ' more below', boardCleared: 'Board cleared',
    pinsFull: '6 pins at most · unpin one first', strip: 'Frames on the board', waitsOnYou: ', waits on you',
    newAnswer: 'New answer, ', showMore: ', show it in full', clearAll: 'Clear: take away every unpinned frame',
    bringBackCleared: 'Bring back the cleared frames', showNew: 'New answer above: show it',
    openBigMeta: 'Open big', backToBoard: 'Esc back to the board'
  };
  // Each kind's glyph (holo's icons, drawn by tokens.css) and its name (the index strip's accessible names).
  var GLYPHS = {text: 'read-all', calendar: 'calendar', mail_thread: 'mail', mail_list: 'inbox', todos: 'todos',
                picture: 'image'};
  var KIND_WORDS = {text: 'Answer', calendar: 'Calendar', mail_thread: 'Mail', mail_list: 'Mail list',
                    todos: 'To-dos', picture: 'Picture'};

  // The renderer table: one function per kind (DESIGN-SPEC 3.3). Each checks the frame's body as board.protocol does
  // (its keys, types, caps and lists) and throws on anything it can't show - the message is then dropped and nothing
  // changes - then fills the body from that data, every word as text. A row whose main words are empty is the
  // "+N MORE" row: only its meta shows.
  var KINDS = Object.create(null);

  KINDS.text = function (body, frame) {
    var data = frame.body;
    if (!onlyKeys(data, ['text', 'rows']) || !words(data.text, CAPS.text)) {
      throw new TypeError('text');
    }
    var rows = data.rows === undefined ? [] : rowsOf(data, ['lead', 'text', 'meta'], function (row) {
      return words(row.lead, CAPS.meta) && words(row.text, CAPS.title) && words(row.meta, CAPS.meta);
    });
    if (data.text) {
      body.appendChild(make('p', 'f-text t-body-sm', data.text));
    }
    if (!rows.length) {
      return;
    }
    var leads = rows.some(function (row) { return row.lead !== ''; });
    var table = make('div', 'tx-rows' + (leads ? ' leads' : ''));
    rows.forEach(function (row) {
      if (!row.lead && !row.text) {
        table.appendChild(more(row.meta));
        return;
      }
      var line = make('div', 'tx-row');
      if (leads) {
        line.appendChild(make('span', 'tx-lead t-frame-time', row.lead));
      }
      line.append(make('span', 'tx-text', row.text), make('span', 'tx-meta', row.meta));
      table.appendChild(line);
    });
    body.appendChild(table);
  };

  KINDS.calendar = function (body, frame) {
    var data = frame.body;
    if (!onlyKeys(data, ['first', 'last', 'now_min', 'ticks', 'spans', 'now', 'rows'])
        || !whole(data.first, 0, DAY_MINUTES) || !whole(data.last, data.first, DAY_MINUTES)
        || !whole(data.now_min, -1, DAY_MINUTES) || !words(data.now, CAPS.pill)
        || !list(data.ticks, CAPS.ticks) || !list(data.spans, CAPS.spans)) {
      throw new TypeError('calendar');
    }
    var bar = data.last > data.first;
    var ticksOk = data.ticks.every(function (tick) {
      return onlyKeys(tick, ['at', 'text']) && whole(tick.at, data.first, data.last) && words(tick.text, CAPS.tick);
    });
    var spansOk = data.spans.every(function (span) {
      return onlyKeys(span, ['start', 'end', 'kind']) && whole(span.start, data.first, data.last)
        && whole(span.end, span.start, data.last) && SPAN_KINDS.indexOf(span.kind) >= 0;
    });
    if (!ticksOk || !spansOk || (data.now_min !== -1 && (!bar || data.now_min < data.first || data.now_min > data.last))
        || (!bar && (data.ticks.length || data.spans.length))) {
      throw new TypeError('calendar');
    }
    var rows = rowsOf(data, ['time', 'title', 'meta', 'state'], function (row) {
      return words(row.time, CAPS.meta) && words(row.title, CAPS.title) && words(row.meta, CAPS.meta)
        && CALENDAR_STATES.indexOf(row.state) >= 0;
    });
    if (bar) {
      var length = data.last - data.first;
      var strip = make('div', 'cal-bar');
      strip.setAttribute('aria-hidden', 'true');
      data.spans.forEach(function (span) {
        var piece = make('i', 'cal-span ' + span.kind);
        place(piece, (span.start - data.first) / length, (span.end - span.start) / length);
        strip.appendChild(piece);
      });
      if (data.now_min >= 0) {
        var tick = make('i', 'cal-tick');
        place(tick, (data.now_min - data.first) / length, 0);
        strip.appendChild(tick);
      }
      var hours = make('div', 'cal-hours');
      hours.setAttribute('aria-hidden', 'true');
      data.ticks.forEach(function (mark) {
        var at = (mark.at - data.first) / length;
        var label = make('span', at <= 0 ? 'start' : at >= 1 ? 'end' : '', mark.text);
        place(label, at, 0);
        hours.appendChild(label);
      });
      body.append(strip, hours);
    }
    var table = make('div', 'cal-rows');
    var horizon = -1;                          // the now-line goes between the past and what comes next
    if (data.now) {
      horizon = rows.length;
      for (var index = 0; index < rows.length; index += 1) {
        if (rows[index].state !== 'past') {
          horizon = index;
          break;
        }
      }
    }
    rows.forEach(function (row, index) {
      if (index === horizon) {
        table.appendChild(nowLine(data.now));
      }
      if (!row.time && !row.title) {
        table.appendChild(more(row.meta));
        return;
      }
      var line = make('div', 'cal-row' + (row.state ? ' ' + row.state : ''));
      line.append(make('span', 'cal-time t-frame-time', row.time), make('span', 'cal-title', row.title),
                  make('span', 'cal-meta', row.meta));
      table.appendChild(line);
    });
    if (horizon === rows.length && horizon >= 0) {
      table.appendChild(nowLine(data.now));
    }
    if (table.firstChild) {
      body.appendChild(table);
    }
  };

  KINDS.mail_thread = function (body, frame) {
    var data = frame.body;
    if (!onlyKeys(data, ['sender', 'time', 'text']) || !words(data.sender, CAPS.title) || !words(data.time, CAPS.meta)
        || !words(data.text, CAPS.text)) {
      throw new TypeError('mail_thread');
    }
    if (data.sender || data.time) {
      var from = make('div', 'mt-from');
      if (data.sender) {
        from.appendChild(make('b', 'mt-sender', data.sender));
      }
      if (data.time) {
        from.appendChild(make('span', 'mt-time t-badge', data.time));
      }
      body.appendChild(from);
    }
    if (data.text) {
      body.appendChild(make('p', 'mt-text', data.text));     // clamped to 3 lines by its style, never in its words
    }
  };

  KINDS.mail_list = function (body, frame) {
    var data = frame.body;
    if (!onlyKeys(data, ['rows'])) {
      throw new TypeError('mail_list');
    }
    var rows = rowsOf(data, ['sender', 'time', 'subject', 'meta', 'unread'], function (row) {
      return words(row.sender, CAPS.title) && words(row.time, CAPS.meta) && words(row.subject, CAPS.title)
        && words(row.meta, CAPS.meta) && typeof row.unread === 'boolean';
    });
    rows.forEach(function (row) {
      if (!row.sender && !row.subject) {
        body.appendChild(more(row.meta));
        return;
      }
      var line = make('div', 'ml-row' + (row.unread ? ' unread' : ''));
      var dot = make('i', 'ml-dot');
      if (row.unread) {
        dot.setAttribute('role', 'img');
        dot.setAttribute('aria-label', WORDS.unread);
      }
      var side = make('span', 'ml-side t-badge');
      if (row.meta) {
        side.appendChild(make('span', 'ml-meta', row.meta));
      }
      if (row.meta && row.time) {
        side.appendChild(make('span', 'ml-dotsep', WORDS.dot));
      }
      if (row.time) {
        side.appendChild(make('span', 'ml-time', row.time));
      }
      line.append(dot, make('span', 'ml-sender', row.sender), side, make('span', 'ml-subject', row.subject));
      body.appendChild(line);
    });
  };

  KINDS.todos = function (body, frame) {
    var data = frame.body;
    if (!onlyKeys(data, ['rows'])) {
      throw new TypeError('todos');
    }
    var rows = rowsOf(data, ['text', 'meta', 'state'], function (row) {
      return words(row.text, CAPS.title) && words(row.meta, CAPS.meta) && TODO_STATES.indexOf(row.state) >= 0;
    });
    rows.forEach(function (row) {
      if (!row.text) {
        body.appendChild(more(row.meta));
        return;
      }
      var line = make('div', 'td-row ' + row.state);
      var box = make('i', 'td-box');
      if (row.state === 'waits') {
        box.appendChild(glyph('diamond', 'td-mark', true));
      } else if (row.state === 'done') {
        box.appendChild(glyph('check', 'td-mark'));
      }
      var state = row.state === 'waits' ? WORDS.waits : row.state === 'done' ? WORDS.done : row.meta;
      line.append(box, make('span', 'td-text', row.text), make('span', 'td-meta t-badge', state));
      body.appendChild(line);
    });
  };

  KINDS.picture = function (body, frame) {
    var data = frame.body;
    if (!onlyKeys(data, ['caption']) || !words(data.caption, CAPS.title)) {
      throw new TypeError('picture');
    }
    var caption = make('p', 'pic-cap');
    caption.append(make('span', 'pic-host', data.caption), make('span', 'pic-only t-badge', WORDS.pictureOnly));
    body.append(make('div', 'pic-slot'), caption);   // its JPEG comes in its own picture message, into the slot
  };

  var root = null;
  var bridge = null;
  var readySent = false;
  var frames = new Map();                      // id -> the frame's record (in arrival order)
  var groups = new Map();                      // id -> a group's record (its section, header and frames' order)
  var barParts = null;                         // the board bar's elements, made with the first bar message
  var stripParts = null;                       // the index strip, "N more below" and the pill (the desk's)
  var clearedRow = null;                       // "Board cleared · 8 FRAMES · Bring back" (the first row of the board)
  var records = new WeakMap();                 // element -> record
  var sections = new WeakMap();                // a group's section -> its record
  var lit = '';
  var litCue = false;                          // the app draws the cue line's native part to the lit frame
  var stub = null;                             // the cue line's last 15 px: {el, id}
  var big = null;                              // open big: {id, rec, card, state: wait / open / closing / shut, box}
  var bigTurn = 0;                             // bumped by each open asked, each step back and each "laid" made ready:
                                               // a "laid" is sent only while its own step is the latest
  var bigLayer = null;                         // the big frame's layer over the whole view
  var backdrop = null;                         // the conversation column as the app painted it
  var focused = '';
  var motion = 'lively';
  var K = 1;                                   // the motion's multiplier, as --k in board.css
  var easeOut = 'cubic-bezier(0.16, 1, 0.3, 1)';
  var batch = 0, batchTimer = 0;
  var layoutTimer = 0, lastLayout = 0, scrolling = false, scrollTimer = 0;
  var viewTimer = 0;
  var filter = 'all';                          // the bar's All | Pinned
  var barPinned = 0;                           // the app's count of pinned frames (its bar message)
  var reading = {pointer: false, focus: false, sentAt: 0, sent: ''};
  var dropped = Object.create(null);

  // ---- small helpers ------------------------------------------------------------------------------------------------

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) {
      node.className = cls;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function glyph(name, cls, filled) {
    var svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('class', 'glyph' + (filled ? ' filled' : '') + (cls ? ' ' + cls : ''));
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    var path = document.createElementNS(SVG, 'path');
    path.setAttribute('class', 'g-' + name);
    svg.appendChild(path);
    return svg;
  }

  function border(cls) {                       // a rect over the frame's border: the traced outline, the changed light
    var svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('class', cls);
    svg.setAttribute('aria-hidden', 'true');
    var rect = document.createElementNS(SVG, 'rect');
    rect.setAttribute('pathLength', '100');
    svg.appendChild(rect);
    return svg;
  }

  function button(cls, label, press) {
    var node = make('button', cls);
    node.type = 'button';
    if (label) {
      node.setAttribute('aria-label', label);
    }
    node.addEventListener('click', function (event) {
      event.stopPropagation();
      press(event);
    });
    return node;
  }

  function words(value, cap) {
    return typeof value === 'string' && value.length <= cap;
  }

  function onlyKeys(value, keys) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return false;
    }
    return Object.keys(value).every(function (key) { return keys.indexOf(key) >= 0; });
  }

  function whole(value, low, high) {           // a whole number from low to high (never a boolean, never 1.5)
    return typeof value === 'number' && Math.floor(value) === value && value >= low && value <= high;
  }

  function list(value, cap) {
    return Array.isArray(value) && value.length <= cap;
  }

  function knownIds(value, cap) {              // a list of frame ids the app sent, each once
    return list(value, cap) && value.every(function (id) { return typeof id === 'string' && frames.has(id); })
      && new Set(value).size === value.length;
  }

  function rowsOf(data, keys, check) {         // a body's rows, each with exactly its keys and checked; else throws
    var rows = data.rows;
    if (!list(rows, CAPS.rows) || !rows.every(function (row) {
      return onlyKeys(row, keys) && keys.every(function (key) { return key in row; }) && check(row);
    })) {
      throw new TypeError('rows');
    }
    return rows;
  }

  function more(text) {                        // the "+N MORE" row: its meta only
    return make('p', 'x-more t-badge', text);
  }

  function place(node, at, width) {            // a mark on the calendar's bar or under it, in fractions of the day
    node.style.setProperty('--at', (Math.max(0, Math.min(1, at)) * 100).toFixed(3) + '%');
    node.style.setProperty('--w', (Math.max(0, Math.min(1, width)) * 100).toFixed(3) + '%');
  }

  function nowLine(text) {                     // the now-line horizon: the pill, then a hairline fading out
    var line = make('div', 'cal-now');
    var rule = make('i', 'cal-rule');
    rule.setAttribute('aria-hidden', 'true');
    line.append(make('span', 'cal-pill', text), rule);
    return line;
  }

  function placePicture(rec) {                // a picture frame's slot (above its caption), else after the body
    if (!rec.picture) {
      return;
    }
    var slot = rec.body.querySelector('.pic-slot');
    if (slot) {
      slot.appendChild(rec.picture);
    } else {
      rec.body.after(rec.picture);
    }
  }

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function reducedMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function drop(why) {
    dropped[why] = (dropped[why] || 0) + 1;
    try {
      console.warn('board: a message was dropped');   // the app counts it and never keeps the words
    } catch (ignored) {
      // nothing to do
    }
    return false;
  }

  function post(kind, payload) {
    if (!bridge) {
      return;
    }
    try {
      bridge.post(kind, JSON.stringify(payload || {}));
    } catch (ignored) {
      // the app is going away
    }
  }

  function after(animation, done) {
    animation.finished.then(done, done);
  }

  function onDesk() {                          // the desk's board (the bar, the strip, parking): not parts 3 and 4's page
    return document.body.classList.contains('has-bar');
  }

  function placeIn(parent, nodes) {           // ``parent`` holds exactly ``nodes`` in order; a node in place never moves
    var cursor = parent.firstChild;
    nodes.forEach(function (node) {
      if (node === cursor) {
        cursor = cursor.nextSibling;
      } else {
        parent.insertBefore(node, cursor);
      }
    });
    while (cursor) {
      var next = cursor.nextSibling;
      parent.removeChild(cursor);
      cursor = next;
    }
  }

  // ---- a frame ------------------------------------------------------------------------------------------------------

  function build(id) {
    var art = make('article', 'frame');
    var rec = {id: id, el: art, kind: '', data: null, pinned: false, closed: false, picture: null,
               closedTimer: 0, drawTimer: 0, outlineTimer: 0, lapTimer: 0, noteTimer: 0, group: '', fact: '',
               sat: false, expanded: false, cleared: false, away: false, paired: '', mates: null, ix: null,
               ixGlyph: null, ixKind: ''};
    records.set(art, rec);
    art.tabIndex = 0;
    art.setAttribute('aria-labelledby', 'title-' + id);
    // A double click opens it big (part 5c), as Enter does; never on its own controls, and it selects no words.
    art.addEventListener('mousedown', function (event) {
      if (event.detail > 1) {
        event.preventDefault();
      }
    });
    art.addEventListener('dblclick', function (event) {
      if ((event.target.closest && event.target.closest('button')) || rec.sat || rec.closed || !current(rec)) {
        return;
      }
      askOpen(id);
    });
    art.appendChild(border('fx-outline'));
    ['tl', 'tr', 'bl', 'br'].forEach(function (corner) {
      art.appendChild(make('span', 'fx-tick ' + corner));
      art.appendChild(make('span', 'fx-bracket ' + corner));
    });
    art.appendChild(border('fx-light'));
    var skeleton = make('div', 'fx-skeleton');
    skeleton.append(make('i'), make('i'), make('i'));
    art.appendChild(skeleton);

    var head = make('div', 'f-head');
    rec.glyph = glyph('read-all', 'f-glyph');
    rec.heading = make('h2', 'f-title t-frame-title');
    rec.heading.id = 'title-' + id;
    rec.tag = make('span', 'f-tag t-badge', WORDS.changed);
    rec.pinMark = glyph('pin', 'f-pinned');
    rec.meta = make('span', 'f-meta t-badge');
    rec.note = make('span', 'f-note t-badge');  // the 7th pin's "6 pins at most": in the meta's place for 4 s
    rec.note.setAttribute('role', 'status');
    var controls = make('div', 'f-ctl');
    rec.pinButton = button('f-icon', WORDS.pin, function () { togglePin(rec); });
    rec.pinButton.appendChild(glyph('pin'));
    var openBig = button('f-icon', WORDS.openBig, function () { askOpen(id); });
    openBig.appendChild(glyph('expand'));
    var closer = button('f-icon', WORDS.close, function () { close(rec); });
    closer.appendChild(glyph('close'));
    controls.append(rec.pinButton, openBig, closer);
    head.append(rec.glyph, rec.heading, rec.tag, rec.pinMark, rec.meta, rec.note, controls);
    art.appendChild(head);

    rec.body = make('div', 'f-body');
    art.appendChild(rec.body);

    // The amber chip opens its card on Approvals (the native one) and does nothing else: a click never even moves the
    // focus (Enter and Space on it, from the keyboard, press it as any button).
    rec.wait = button('f-wait', '', function () { post('go_approvals', {id: id}); });
    rec.wait.addEventListener('mousedown', function (event) { event.preventDefault(); });
    rec.waitWords = make('span', 'f-words');
    rec.wait.append(glyph('diamond', 'f-dia', true), rec.waitWords, glyph('chevron', 'f-chev'),
                    make('span', 'f-key t-kbd', WORDS.approvalsKey));
    art.appendChild(rec.wait);

    var keys = make('div', 'f-keys t-badge');
    keys.append(make('span', '', WORDS.keysFirst), make('span', 'mid', WORDS.dot), make('span', '', WORDS.keysSecond));
    art.appendChild(keys);

    var closedRow = make('div', 'f-closed');
    rec.closedWhat = make('span', 'f-what t-body-sm');
    rec.bringBack = button('f-act', '', function () { bringBack(rec); });
    rec.bringBack.textContent = WORDS.bringBack;
    closedRow.append(glyph('close'), rec.closedWhat, rec.bringBack);
    art.appendChild(closedRow);

    // The satellite (DESIGN-SPEC 3.3): an older answer's unpinned frame as one 48 px row - its glyph in a 30 px
    // square, its title and one fact - that a click or Enter expands in place.
    rec.satButton = button('f-sat', '', function () { expand(rec); });
    rec.satButton.tabIndex = -1;
    rec.satButton.setAttribute('aria-expanded', 'false');
    rec.satGlyphBox = make('span', 's-glyph');
    rec.satGlyph = glyph('read-all');
    rec.satGlyphBox.appendChild(rec.satGlyph);
    rec.satTitle = make('span', 's-title');
    rec.satFact = make('span', 's-fact t-badge');
    rec.satFactWords = make('span', 's-words');
    rec.satNote = make('span', 's-note');      // the 7th pin's note, in the fact's place while folded
    rec.satFact.append(glyph('diamond', 's-dia', true), rec.satFactWords, rec.satNote);
    rec.satButton.append(rec.satGlyphBox, rec.satTitle, rec.satFact);
    art.appendChild(rec.satButton);
    updatePinned(rec, false);
    return rec;
  }

  function makeBody(message) {
    var body = make('div', 'f-body');
    if (message.status === 'ok') {
      KINDS[message.kind](body, message);
      return body;
    }
    KINDS[message.kind](make('div'), message);  // the kind's data is checked all the same
    if (message.status === 'failed') {
      var row = make('div', 'f-fail');
      row.append(glyph('warning'), make('p', 'f-words t-body-sm', message.status_text));
      var retry = button('f-act alert', '', function () { post('retry', {id: message.id}); });
      retry.append(glyph('replay'), make('span', '', WORDS.tryAgain));
      body.append(row, retry);
    } else {
      var empty = make('div', 'f-empty');
      var column = make('div');
      column.appendChild(make('p', 'f-words t-body-sm', message.status_text));
      if (message.status_meta) {
        column.appendChild(make('span', 'f-checked t-badge', message.status_meta));
      }
      empty.append(make('span', 'f-ring'), column);
      body.appendChild(empty);
    }
    return body;
  }

  function fill(rec, message, body) {
    if (rec.kind !== message.kind) {
      var name = GLYPHS[message.kind] || 'read-all';
      var mark = glyph(name, 'f-glyph');
      rec.glyph.replaceWith(mark);
      rec.glyph = mark;
      var satMark = glyph(name);
      rec.satGlyph.replaceWith(satMark);
      rec.satGlyph = satMark;
      rec.kind = message.kind;
    }
    rec.heading.textContent = message.title;
    rec.meta.textContent = message.meta;
    rec.body.replaceWith(body);
    rec.body = body;
    placePicture(rec);                         // an update in place keeps the frame's picture
    rec.waitWords.textContent = message.waits;
    rec.el.classList.toggle('waits', message.waits !== '');
    rec.el.classList.toggle('failed', message.status === 'failed');
    rec.el.classList.toggle('empty', message.status === 'empty');
    rec.closedWhat.replaceChildren(document.createTextNode(WORDS.closed), make('b', '', message.title));
    rec.fact = message.fact === undefined ? '' : message.fact;
    rec.satTitle.textContent = message.title;
    rec.satFactWords.textContent = rec.fact;
    rec.satButton.setAttribute('aria-label', message.title + (rec.fact ? ', ' + rec.fact : '') + WORDS.showMore);
    updatePinned(rec, message.pinned);
    rec.data = message;
    if (rec.ix) {
      labelIndex(rec);
    }
  }

  function differs(before, now) {
    return JSON.stringify([before.title, before.meta, before.body, before.status, before.status_text])
      !== JSON.stringify([now.title, now.meta, now.body, now.status, now.status_text]);
  }

  function validFrame(message) {
    return onlyKeys(message, FRAME_FIELDS)
      && typeof message.id === 'string' && ID.test(message.id)
      && typeof message.kind === 'string' && message.kind in KINDS
      && words(message.title, CAPS.title) && words(message.meta, CAPS.meta)
      && words(message.status_text, CAPS.status_text) && words(message.status_meta, CAPS.status_meta)
      && words(message.waits, CAPS.waits) && STATUSES.indexOf(message.status) >= 0
      && typeof message.changed === 'boolean' && typeof message.pinned === 'boolean'
      && typeof message.closed === 'boolean'
      && (message.fact === undefined || words(message.fact, CAPS.meta))     // a satellite's fact: a meta's length
      && (message.group === undefined || (typeof message.group === 'string' && groups.has(message.group)))
      && !!message.body && typeof message.body === 'object' && !Array.isArray(message.body);
  }

  // ---- groups: one answer each, the newest on top (DESIGN-SPEC 3.1, 3.5) ----------------------------------------

  function makeGroup(id) {
    var section = make('section', 'group');
    section.setAttribute('aria-labelledby', 'group-' + id);
    var head = make('div', 'g-head');
    head.id = 'group-' + id;
    var rule = make('i', 'g-rule');
    rule.setAttribute('aria-hidden', 'true');
    var group = {id: id, el: section, time: make('span', 'g-time t-badge'), topic: make('span', 'g-topic'),
                 list: make('div', 'g-frames'), order: [], older: false, parked: false, seen: false,
                 rule: make('i', 'ix-rule')};
    group.rule.setAttribute('aria-hidden', 'true');
    sections.set(section, group);
    head.append(group.time, group.topic, rule);
    section.append(head, group.list);
    return group;
  }

  function arrange(group) {                    // the group's frames in its order (a frame not in it stays at the end)
    var cursor = group.list.firstChild;
    group.order.forEach(function (id) {
      var rec = frames.get(id);
      if (!rec || rec.group !== group.id) {
        return;
      }
      if (rec.el === cursor) {
        cursor = cursor.nextSibling;
      } else {
        group.list.insertBefore(rec.el, cursor);
      }
    });
  }

  function placeFrame(rec, groupId) {          // a new frame: at its group's end, else at the board's
    var group = groupId ? groups.get(groupId) : null;
    if (group) {
      rec.group = group.id;
      group.list.appendChild(rec.el);
      arrange(group);
    } else {
      root.appendChild(rec.el);
    }
  }

  function groupOf(rec) {
    return rec.group ? groups.get(rec.group) || null : null;
  }

  function parked(rec) {                       // its answer waits out of sight (the pill), the board as it was
    var group = groupOf(rec);
    return !!(group && group.parked);
  }

  function current(rec) {                      // a frame (or a group) the board still has
    return !!rec && (rec.list ? groups.get(rec.id) === rec : frames.get(rec.id) === rec);
  }

  function boardFrames() {                     // the frames shown, in board order (every group's, top to bottom)
    return Array.prototype.filter.call(root.querySelectorAll('.frame'), function (node) {
      var rec = records.get(node);
      return current(rec) && !rec.closed && !rec.away && !parked(rec);
    }).map(function (node) { return records.get(node); });
  }

  function target(rec) {                       // what takes the keyboard focus for a frame (a satellite: its row)
    return rec.sat ? rec.satButton : rec.el;
  }

  // ---- the board's arrangement: satellites, what is out of sight, pairs, the strip ---------------------------------

  function refresh() {
    if (!root) {
      return;
    }
    var hadFocus = document.activeElement;
    var heldBy = hadFocus && hadFocus.closest ? hadFocus.closest('.frame') : null;
    frames.forEach(function (rec) {
      var group = groupOf(rec);
      rec.away = rec.cleared || (filter === 'pinned' && !rec.pinned);
      rec.sat = !!group && group.older && !rec.pinned && !rec.expanded && !rec.closed;
      rec.el.classList.toggle('away', rec.away);
      rec.el.classList.toggle('sat', rec.sat);
      rec.el.tabIndex = rec.sat || rec.closed ? -1 : 0;
      rec.satButton.tabIndex = rec.sat ? 0 : -1;
    });
    pairUp();
    groups.forEach(function (group) {
      var shown = !group.parked && Array.prototype.some.call(group.list.children, function (node) {
        var rec = records.get(node);
        return current(rec) && !rec.away;
      });
      group.el.classList.toggle('parked', group.parked);
      group.el.classList.toggle('hollow', !group.parked && !shown);
      if (shown && !group.seen) {
        group.seen = true;
        makeRoom(group);
      }
    });
    var held = heldBy ? records.get(heldBy) : null;
    if (current(held) && !held.away && !held.closed) {  // a frame that folded or unfolded keeps the focus
      var want = target(held);
      var active = document.activeElement;
      if (active !== want && (active === held.el || active === held.satButton || held.sat)) {
        want.focus({preventScroll: true});
      }
    }
    syncBar();
    syncCleared();
    syncPill();
    buildStrip();
    scheduleView();
    scheduleLayout();
  }

  function pairUp() {                          // small kinds side by side: two from a 500 px area, three from 756 (2.3)
    var across = root.classList.contains('trios') ? 3 : root.classList.contains('pairs') ? 2 : 1;
    frames.forEach(function (rec) {
      rec.paired = '';
      rec.mates = null;
    });
    groups.forEach(function (group) {
      var run = [];
      function rowUp() {                       // adjacent small kinds, at most ``across`` a row; one alone stays full
        while (run.length) {
          var row = run.splice(0, Math.min(across, run.length));
          if (row.length > 1) {
            row.forEach(function (rec, index) {
              rec.paired = 'abc'.charAt(index);
              rec.mates = row;
            });
          }
        }
      }
      Array.prototype.forEach.call(group.list.children, function (node) {
        var rec = records.get(node);
        if (!current(rec) || rec.away) {
          return;                              // out of sight: it neither joins a row nor breaks one
        }
        if (across > 1 && SMALL_KINDS.indexOf(rec.kind) >= 0 && !rec.sat && !rec.closed) {
          run.push(rec);
        } else {
          rowUp();
        }
      });
      rowUp();
    });
    frames.forEach(function (rec) {
      var size = rec.mates ? rec.mates.length : 0;
      rec.el.classList.toggle('pair-a', size === 2 && rec.paired === 'a');
      rec.el.classList.toggle('pair-b', size === 2 && rec.paired === 'b');
      rec.el.classList.toggle('trio', size === 3);
    });
  }

  function measureWidth(boxWidth) {            // the frames area's width: pairs from 500 px, three across from 756
    if (!root || (big && boxWidth === undefined)) {
      return;                                  // open big: the board under the card keeps the layout it had (it is
    }                                          // measured again once the view is its own size)
    var style = getComputedStyle(root);
    var outer = boxWidth === undefined ? root.clientWidth : boxWidth - (window.innerWidth - root.clientWidth);
    var width = outer - parseFloat(style.paddingLeft || '0') - parseFloat(style.paddingRight || '0');
    var pairs = width >= PAIRS_FROM, trios = width >= TRIOS_FROM;
    if (pairs !== root.classList.contains('pairs') || trios !== root.classList.contains('trios')) {
      root.classList.toggle('pairs', pairs);
      root.classList.toggle('trios', trios);
      refresh();
    }
  }

  function makeRoom(group) {                   // a group arrives: room made for it, 220 ms expo-out (5)
    if (K === 0 || !group.el.animate || !onDesk()) {
      return;
    }
    var el = group.el;
    el.classList.add('making');                // no room yet while the answer's frames come in: nothing jumps
    setTimeout(function () {
      var height = el.scrollHeight;
      el.classList.remove('making');
      if (!height || !el.isConnected) {
        return;
      }
      el.classList.add('sizing');
      after(el.animate([{height: '0px'}, {height: height + 'px'}], {duration: ROOM_MS * K, easing: easeOut}),
            function () {
              el.classList.remove('sizing');
              scheduleView();
              scheduleLayout();                // the frames moved: the cue line's other end follows
            });
    }, ROOM_WAIT_MS);
  }

  // ---- motion -------------------------------------------------------------------------------------------------------

  function setMotion(level) {
    motion = level;
    var html = document.documentElement;
    html.classList.remove('motion-lively', 'motion-calm', 'motion-off');
    html.classList.add('motion-' + level);
    K = reducedMotion() ? 0 : MOTIONS[level];
  }

  function landed(rec, step) {                 // the board's sounds (part 5c): the app hears a frame land, then settle
    if (current(rec) && onDesk()) {
      post('landed', {id: rec.id, step: step});
    }
  }

  function arrive(rec) {                       // the outline traces, the ticks snap on, the content comes in
    var index = batch;
    batch += 1;
    clearTimeout(batchTimer);
    batchTimer = setTimeout(function () { batch = 0; }, BATCH_MS);
    if (K === 0 || index >= MAX_ANIMATED) {
      landed(rec, 'done');                     // filled at once
      return;
    }
    rec.el.style.setProperty('--i', String(index));
    rec.el.classList.add('arriving');
    clearTimeout(rec.drawTimer);
    clearTimeout(rec.outlineTimer);
    rec.outlineTimer = setTimeout(function () { landed(rec, 'outline'); }, (index * STAGGER_MS + OUTLINE_MS) * K);
    rec.drawTimer = setTimeout(function () {
      rec.el.classList.remove('arriving');
      rec.el.style.removeProperty('--i');
      if (rec.ix) {
        rec.ix.classList.remove('arr');
      }
      scheduleLayout();
      landed(rec, 'done');
    }, (index * STAGGER_MS + DRAW_MS) * K + 50);
  }

  function lap(rec) {                          // one light round the border, then CHANGED stays
    var el = rec.el;
    clearTimeout(rec.lapTimer);
    el.classList.remove('lap');
    if (motion !== 'lively' || K === 0) {
      el.classList.add('changed');             // calm: the badge only; off: the end state
      return;
    }
    el.classList.remove('changed');
    void el.getBoundingClientRect();           // the lap starts again from the top
    el.classList.add('lap');
    rec.lapTimer = setTimeout(function () {
      el.classList.remove('lap');
      el.classList.toggle('changed', !!(rec.data && rec.data.changed));
    }, LAP_MS * K + 30);
  }

  function resize(el, from, done) {            // close / bring back / expand: fade 160 ms, height 200 ms
    if (K === 0 || !el.animate) {
      if (done) {
        done();
      }
      return;
    }
    var to = el.getBoundingClientRect().height;
    el.classList.add('sizing');
    el.animate([{opacity: 0.25}, {opacity: 1}], {duration: FADE_MS * K, easing: easeOut});
    after(el.animate([{height: from + 'px'}, {height: to + 'px'}], {duration: HEIGHT_MS * K, easing: easeOut}),
          function () {
            el.classList.remove('sizing');
            scheduleView();
            scheduleLayout();                  // the frames below moved: the cue line's other end follows
            if (done) {
              done();
            }
          });
  }

  // ---- what the owner does on a frame ---------------------------------------------------------------------------------

  function updatePinned(rec, on) {
    rec.pinned = !!on;
    rec.el.classList.toggle('pinned', rec.pinned);
    rec.pinButton.classList.toggle('on', rec.pinned);
    rec.pinButton.setAttribute('aria-label', rec.pinned ? WORDS.unpin : WORDS.pin);
    rec.pinButton.setAttribute('aria-pressed', rec.pinned ? 'true' : 'false');
  }

  function pinnedCount() {
    var count = 0;
    frames.forEach(function (rec) {
      count += rec.pinned ? 1 : 0;
    });
    return count;
  }

  function note(rec, text) {                  // a few words in the meta's place for NOTE_MS (the 7th pin's why)
    clearTimeout(rec.noteTimer);
    rec.note.textContent = text;
    rec.satNote.textContent = text;
    rec.el.classList.add('noted');
    rec.noteTimer = setTimeout(function () {
      rec.el.classList.remove('noted');
      rec.note.textContent = '';
      rec.satNote.textContent = '';
    }, NOTE_MS);
  }

  function togglePin(rec) {
    if (!rec.pinned && pinnedCount() >= MAX_PINS) {
      note(rec, WORDS.pinsFull);               // never a 7th: the frame says why, and stays as it is
      return;
    }
    var from = rec.el.getBoundingClientRect().height;
    var wasSat = rec.sat;
    updatePinned(rec, !rec.pinned);
    post('pin', {id: rec.id, on: rec.pinned});
    refresh();
    if (wasSat && !rec.sat) {
      resize(rec.el, from);                    // a pinned satellite stays full
    }
  }

  function setClosed(rec, closed, byOwner) {
    var el = rec.el;
    var from = el.getBoundingClientRect().height;
    var hadFocus = el.contains(document.activeElement);
    clearTimeout(rec.closedTimer);
    rec.closed = closed;
    el.classList.toggle('closed', closed);
    el.classList.remove('expired');
    if (closed) {
      // Bring back is offered CLOSED_MS; the app takes the frame away then (it is the source of truth).
      rec.closedTimer = setTimeout(function () { el.classList.add('expired'); }, CLOSED_MS);
    }
    refresh();
    if (closed) {
      if (hadFocus) {
        rec.bringBack.focus();
      }
    } else if (byOwner) {
      target(rec).focus();
    }
    if (byOwner) {
      resize(el, from);
    }
  }

  function close(rec) {
    if (!rec.closed) {
      setClosed(rec, true, true);
      post('close', {id: rec.id});
    }
  }

  function bringBack(rec) {
    if (rec.closed) {
      setClosed(rec, false, true);
      post('bring_back', {id: rec.id});
    }
  }

  function expand(rec) {                       // a satellite unfolds in place (the focus goes with it)
    if (!rec.sat) {
      return;
    }
    var from = rec.el.getBoundingClientRect().height;
    rec.expanded = true;
    rec.satButton.setAttribute('aria-expanded', 'true');
    refresh();
    resize(rec.el, from);
  }

  function release(rec) {                      // the frame is no longer the board's (its element may stay a moment)
    if (frames.get(rec.id) === rec) {
      frames.delete(rec.id);
    }
    clearTimeout(rec.closedTimer);
    clearTimeout(rec.drawTimer);
    clearTimeout(rec.outlineTimer);
    clearTimeout(rec.lapTimer);
    clearTimeout(rec.noteTimer);
    if (lit === rec.id) {
      lit = '';
      litCue = false;
      root.classList.remove('has-lit');
      fadeStub();
    }
    if (big && big.id === rec.id) {
      shrink(false);                           // its frame left the board: back at once (nothing to go back to)
    }
    if (focused === rec.id) {
      focused = '';
    }
  }

  function forget(rec, animate) {              // the frame leaves the board
    release(rec);
    var el = rec.el;
    if (!animate || K === 0 || !el.animate || rec.away || parked(rec)) {
      el.remove();
      refresh();
      return;
    }
    var height = el.getBoundingClientRect().height;
    el.classList.add('sizing');
    after(el.animate([{opacity: 1}, {opacity: 0}], {duration: FADE_MS * K, easing: easeOut, fill: 'forwards'}),
          function () {
            var gone = {height: '0px', paddingTop: '0px', paddingBottom: '0px', borderTopWidth: '0px',
                        borderBottomWidth: '0px', marginTop: '-12px'};
            after(el.animate([{height: height + 'px'}, gone], {duration: HEIGHT_MS * K, easing: easeOut, fill: 'forwards'}),
                  function () {
                    el.remove();
                    refresh();
                  });
          });
    refresh();
  }

  // ---- the keyboard (DESIGN-SPEC 3.6) -----------------------------------------------------------------------------

  function rows() {                            // the frames shown as rows: a pair or three across is one row (Left / Right)
    var found = [];
    boardFrames().forEach(function (rec) {
      var last = found[found.length - 1];
      if (rec.mates && rec.mates[0] !== rec && last && last[0] === rec.mates[0]) {
        last.push(rec);
      } else {
        found.push([rec]);
      }
    });
    return found;
  }

  function goTo(rec) {
    if (!rec) {
      return;
    }
    var node = target(rec);
    node.focus({preventScroll: true});
    node.scrollIntoView({block: 'nearest', behavior: K === 0 ? 'auto' : 'smooth'});
  }

  function move(rec, key) {
    var all = rows();
    var at = -1, column = 0;
    all.forEach(function (row, index) {
      var found = row.indexOf(rec);
      if (found >= 0) {
        at = index;
        column = found;
      }
    });
    if (at < 0) {
      return;
    }
    var row = all[at];
    if (key === 'ArrowLeft' || key === 'ArrowRight') {
      goTo(row[column + (key === 'ArrowRight' ? 1 : -1)]);
    } else if (key === 'Home' || key === 'End') {
      var edge = key === 'Home' ? all[0] : all[all.length - 1];
      goTo(edge && edge[0]);
    } else {
      var next = all[at + (key === 'ArrowDown' ? 1 : -1)];
      goTo(next && next[Math.min(column, next.length - 1)]);
    }
  }

  var MOVES = ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'];

  function onKey(event) {
    if (event.ctrlKey || event.altKey || event.metaKey) {
      return;
    }
    document.documentElement.classList.add('keys');   // the keyboard's own focus shows (as :focus-visible)
    signOfReading();
    var key = event.key;
    if (big && (big.state === 'wait' || big.state === 'open')) {
      if (key === 'Escape') {
        shrink(true);                          // Esc leaves open big first (the board again; the next Esc: the ask bar)
        event.preventDefault();
      } else if (key === 'Tab') {
        cardTab(event.shiftKey);               // the focus stays in the big frame (the board under it is inert)
        event.preventDefault();
      }
      return;                                  // the board's own keys wait under the big frame
    }
    if (key === 'Escape') {
      post('focus_out', {to: 'ask'});        // Esc with nothing big: back to the ask bar
      event.preventDefault();
      return;
    }
    var node = event.target;
    if (stripParts && stripParts.list.contains(node)) {
      stripKey(event);
      return;
    }
    var frameEl = node && node.closest ? node.closest('.frame') : null;
    var rec = frameEl ? records.get(frameEl) : null;
    if (!current(rec) || rec.closed || (node !== rec.el && node !== rec.satButton)) {
      return;                                  // a control inside a frame keeps its own keys
    }
    if (key === 'Enter') {
      if (node === rec.satButton) {
        return;                                // a satellite's row is a button: Enter presses it (it expands)
      }
      askOpen(rec.id);                         // part 5c opens it big
    } else if (key === 'p' || key === 'P') {
      togglePin(rec);
    } else if (key === 'Delete') {
      close(rec);
    } else if (MOVES.indexOf(key) >= 0) {
      move(rec, key);
    } else {
      return;
    }
    event.preventDefault();
  }

  function onFocusIn(event) {
    var node = event.target && event.target.closest ? event.target.closest('.frame') : null;
    var rec = node ? records.get(node) : null;
    if (rec && rec.id !== focused && frames.get(rec.id) === rec) {
      focused = rec.id;
      post('focus', {id: rec.id});
    }
  }

  function onFocusOut(event) {
    if (!root.contains(event.relatedTarget)) {
      focused = '';
    }
  }

  // ---- the owner at the board: the app parks a new answer while it is read (DESIGN-SPEC 3.5) ----------------------

  function sendReading(sign) {
    if (!onDesk()) {
      return;
    }
    var now = Date.now();
    var state = (reading.pointer ? 'p' : '-') + (reading.focus ? 'f' : '-');
    if (state === reading.sent && (!sign || now - reading.sentAt < READING_MS)) {
      return;
    }
    reading.sent = state;
    reading.sentAt = now;
    post('reading', {pointer: reading.pointer, focus: reading.focus});
  }

  function signOfReading() {
    sendReading(true);
  }

  function pageFocused() {
    return document.hasFocus() && !!document.activeElement && document.activeElement !== document.body;
  }

  function watchReading() {
    var html = document.documentElement;
    html.addEventListener('mouseenter', function () {
      reading.pointer = true;
      sendReading(true);
    });
    html.addEventListener('mouseleave', function () {
      reading.pointer = false;
      sendReading(true);
    });
    document.addEventListener('focusin', function () {
      reading.focus = true;
      sendReading(false);
    });
    document.addEventListener('focusout', function () {
      setTimeout(function () {
        reading.focus = pageFocused();
        sendReading(false);
      }, 0);
    });
    window.addEventListener('blur', function () {
      reading.focus = false;
      sendReading(false);
    });
    document.addEventListener('wheel', signOfReading, {passive: true, capture: true});
    document.addEventListener('pointerdown', function () {
      html.classList.remove('keys');           // a press: no keyboard focus ring until the next key
      signOfReading();
    }, {passive: true, capture: true});
  }

  // ---- the bar's All | Pinned and Clear, the cleared row, the pill ----------------------------------------------------

  function setFilter(which) {
    if (which === filter) {
      return;
    }
    filter = which;
    refresh();
    root.scrollTo({top: 0});
  }

  function syncBar() {
    if (!barParts) {
      return;
    }
    var clearable = false;
    frames.forEach(function (rec) {
      clearable = clearable || (!rec.pinned && !rec.cleared && !parked(rec));
    });
    barParts.all.classList.toggle('on', filter === 'all');
    barParts.all.setAttribute('aria-pressed', filter === 'all' ? 'true' : 'false');
    barParts.pinned.classList.toggle('on', filter === 'pinned');
    barParts.pinned.setAttribute('aria-pressed', filter === 'pinned' ? 'true' : 'false');
    barParts.pinned.disabled = barPinned === 0 && filter !== 'pinned';
    barParts.clear.disabled = !clearable;
  }

  function makeCleared() {                     // the board's first row: "Board cleared · 8 FRAMES · Bring back"
    var row = make('div', 'cleared-row');
    row.setAttribute('role', 'status');
    var count = make('span', 'cr-count t-badge');
    var back = button('f-act', WORDS.bringBackCleared, function () { post('restore', {}); });
    back.textContent = WORDS.bringBack;
    row.append(glyph('board'), make('span', 'cr-words', WORDS.boardCleared), count, back);
    return {el: row, count: count, back: back};
  }

  function syncCleared() {
    if (!clearedRow) {
      return;
    }
    var count = 0;
    frames.forEach(function (rec) {
      count += rec.cleared ? 1 : 0;
    });
    clearedRow.count.textContent = count + (count === 1 ? WORDS.frame : WORDS.frames);
    clearedRow.el.classList.toggle('shown', count > 0);
  }

  function syncPill() {
    if (!stripParts) {
      return;
    }
    var any = false;
    groups.forEach(function (group) {
      any = any || group.parked;
    });
    stripParts.pill.classList.toggle('shown', any && onDesk());
  }

  // ---- the index strip, "N more below" and the fades (DESIGN-SPEC 3.1) ----------------------------------------------

  function makeStrip() {
    var nav = make('nav', '');
    nav.id = 'strip';
    nav.setAttribute('aria-label', WORDS.strip);
    var stripList = make('div', 's-list');
    var brace = make('span', 'ix-brace');
    brace.setAttribute('aria-hidden', 'true');
    stripList.appendChild(brace);
    nav.appendChild(stripList);
    var moreBelow = make('div', 't-badge');
    moreBelow.id = 'more';
    moreBelow.setAttribute('aria-hidden', 'true');    // the strip says the same to a screen reader
    var moreWords = make('span', '');
    moreBelow.append(glyph('chevron', 'more-chev'), moreWords);
    var pill = make('div', '');
    pill.id = 'pill';
    var pillButton = button('pill-btn', WORDS.showNew, function () { post('show_new', {}); });
    pillButton.append(glyph('send-arrow'), make('span', 'pill-words', WORDS.newAbove),
                      make('span', 'pill-show', WORDS.show));
    pill.appendChild(pillButton);
    document.body.append(nav, moreBelow, pill);
    return {nav: nav, list: stripList, brace: brace, more: moreBelow, moreWords: moreWords, pill: pill,
            pillButton: pillButton, roving: null};
  }

  function labelIndex(rec) {
    var title = rec.data ? rec.data.title : '';
    var name = (KIND_WORDS[rec.kind] || KIND_WORDS.text) + ': ' + title;
    if (rec.data && rec.data.waits) {
      name += WORDS.waitsOnYou;
    }
    rec.ix.setAttribute('aria-label', (parked(rec) ? WORDS.newAnswer : '') + name);
    if (rec.ixKind !== rec.kind) {             // the kind's glyph (the diamond pip after it stays)
      var fresh = glyph(GLYPHS[rec.kind] || 'read-all');
      if (rec.ixGlyph) {
        rec.ixGlyph.replaceWith(fresh);
      } else {
        rec.ix.insertBefore(fresh, rec.ix.firstChild);
      }
      rec.ixGlyph = fresh;
      rec.ixKind = rec.kind;
    }
  }

  function indexButton(rec) {
    if (!rec.ix) {
      rec.ix = button('ix', '', function (event) { onIndex(rec, event.detail === 0); });
      rec.ix.tabIndex = -1;
      rec.ix.appendChild(glyph('diamond', 'ix-pip', true));
      labelIndex(rec);
    }
    return rec.ix;
  }

  function onIndex(rec, byKeys) {             // a click scrolls to that frame (the keyboard's Enter also focuses it)
    if (!current(rec)) {
      return;
    }
    if (parked(rec)) {
      post('show_new', {});                    // the parked answer's dashed entry: Show
      return;
    }
    root.scrollTo({top: Math.max(0, rec.el.offsetTop - IN_VIEW_TOP), behavior: K === 0 ? 'auto' : 'smooth'});
    if (byKeys) {
      target(rec).focus({preventScroll: true});
    }
  }

  function buildStrip() {
    if (!stripParts) {
      return;
    }
    var nodes = [];
    var waiting = [];
    groups.forEach(function (group) {          // the parked answers' dashed entries on top, then a rule
      if (group.parked) {
        waiting.push(group);
      }
    });
    waiting.reverse().forEach(function (group) {
      Array.prototype.forEach.call(group.list.children, function (node) {
        var rec = records.get(node);
        if (current(rec)) {
          var ix = indexButton(rec);
          ix.className = 'ix arr parked' + (rec.data && rec.data.waits ? ' wait' : '');
          labelIndex(rec);
          nodes.push(ix);
        }
      });
    });
    if (nodes.length) {
      nodes.push(waiting[0].rule);
    }
    var first = true;
    Array.prototype.forEach.call(root.children, function (section) {
      var group = sections.get(section);
      if (!group || !current(group) || group.parked) {
        return;
      }
      var members = [];
      Array.prototype.forEach.call(group.list.children, function (node) {
        var rec = records.get(node);
        if (current(rec) && !rec.away && !rec.closed) {
          var ix = indexButton(rec);
          var keep = ix.classList.contains('in') ? ' in' : '';
          ix.className = 'ix' + keep + (rec.data && rec.data.waits ? ' wait' : '')
            + (rec.el.classList.contains('arriving') ? ' arr' : '');
          labelIndex(rec);
          members.push(ix);
        }
      });
      if (!members.length) {
        return;
      }
      if (!first) {
        nodes.push(group.rule);
      }
      first = false;
      nodes = nodes.concat(members);
    });
    nodes.push(stripParts.brace);
    placeIn(stripParts.list, nodes);
  }

  function scheduleView() {
    if (viewTimer) {
      return;
    }
    viewTimer = setTimeout(updateView, VIEW_MS);
  }

  function updateView() {                      // what is in view: the strip's brace, "N more below", the top fade
    clearTimeout(viewTimer);
    viewTimer = 0;
    if (!root || !stripParts) {
      return;
    }
    var desk = onDesk();
    root.classList.toggle('scrolled', root.scrollTop > 0);
    var box = root.getBoundingClientRect();
    var top = box.top + IN_VIEW_TOP, end = box.bottom - IN_VIEW_END, edge = box.bottom - EDGE;
    var below = 0, first = null, last = null;
    boardFrames().forEach(function (rec) {
      var place = rec.el.getBoundingClientRect();
      var seen = place.height > 0 && place.top < end && place.bottom > top;
      if (rec.ix) {
        rec.ix.classList.toggle('in', seen);
        if (seen) {
          first = first || rec.ix;
          last = rec.ix;
        }
      }
      if (place.height > 0 && place.bottom > edge) {
        below += 1;                            // not fully in view (inside the last 16 px or past it)
      }
    });
    stripParts.moreWords.textContent = below + WORDS.moreBelow;
    stripParts.more.classList.toggle('shown', desk && below > 0);
    var brace = stripParts.brace;
    if (first && first.parentNode === stripParts.list) {
      brace.style.setProperty('top', (first.offsetTop - 2) + 'px');
      brace.style.setProperty('height', (last.offsetTop + last.offsetHeight - first.offsetTop + 4) + 'px');
      brace.classList.add('shown');
      var list = stripParts.list;              // the brace stays in sight when the strip itself scrolls
      if (first.offsetTop - 4 < list.scrollTop) {
        list.scrollTop = Math.max(0, first.offsetTop - 4);
      } else if (last.offsetTop + last.offsetHeight + 4 > list.scrollTop + list.clientHeight) {
        list.scrollTop = Math.min(first.offsetTop - 4, last.offsetTop + last.offsetHeight + 4 - list.clientHeight);
      }
    } else {
      brace.classList.remove('shown');
    }
    rove(first);
    placeStub();
  }

  function stripButtons() {
    return Array.prototype.filter.call(stripParts.list.querySelectorAll('.ix'), function (node) {
      return node.parentNode === stripParts.list;
    });
  }

  function rove(preferred) {                   // one stop in the strip for Tab: the button in view (or the focused one)
    var all = stripButtons();
    var keep = all.indexOf(document.activeElement) >= 0 ? document.activeElement : null;
    var stop = keep || (preferred && all.indexOf(preferred) >= 0 ? preferred : all[0]) || null;
    all.forEach(function (node) {
      node.tabIndex = node === stop ? 0 : -1;
    });
    stripParts.roving = stop;
  }

  function stripKey(event) {
    var all = stripButtons();
    var at = all.indexOf(event.target);
    if (at < 0) {
      return;
    }
    var key = event.key, next = null;
    if (key === 'ArrowDown' || key === 'ArrowRight') {
      next = all[Math.min(all.length - 1, at + 1)];
    } else if (key === 'ArrowUp' || key === 'ArrowLeft') {
      next = all[Math.max(0, at - 1)];
    } else if (key === 'Home') {
      next = all[0];
    } else if (key === 'End') {
      next = all[all.length - 1];
    } else {
      return;                                  // Enter and Space press the button: it shows its frame
    }
    event.preventDefault();
    all.forEach(function (node) {
      node.tabIndex = node === next ? 0 : -1;
    });
    next.focus();
  }

  // ---- where the frames are (for the cue line) ------------------------------------------------------------------------

  function sendLayout() {
    lastLayout = Date.now();
    var places = [];
    frames.forEach(function (rec) {
      var box = rec.el.getBoundingClientRect();
      places.push({id: rec.id, top: Math.round(box.top), height: Math.round(box.height)});
    });
    post('layout', {frames: places.slice(0, MAX_FRAMES), scrolling: scrolling});
  }

  function scheduleLayout() {
    if (layoutTimer || !bridge) {
      return;
    }
    layoutTimer = setTimeout(function () {
      layoutTimer = 0;
      sendLayout();
    }, Math.max(0, LAYOUT_MS - (Date.now() - lastLayout)));
  }

  function onScroll() {
    if (!scrolling) {
      scrolling = true;
      scheduleLayout();
      placeStub();                             // the cue line hides while the board scrolls ...
    }
    scheduleView();
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(function () {
      scrolling = false;
      scheduleLayout();
      placeStub();                             // ... and is back 150 ms after it stops, without the draw-in
    }, SCROLL_END_MS);
  }

  // ---- the lit frame's cue line: its last 15 px, from the view's left edge to the frame (DESIGN-SPEC 8) -------------

  function cueY(rec) {                         // where the cue line meets the frame: desk_cue.anchor's whole numbers
    var box = root.getBoundingClientRect(), place = rec.el.getBoundingClientRect();
    var top = Math.round(place.top);
    var low = Math.max(top, Math.round(box.top));
    var high = Math.min(top + Math.round(place.height), Math.round(box.bottom));
    return high - low < MIN_SEEN ? null : Math.floor((low + high) / 2);
  }

  function fadeStub() {                        // 400 ms after its sentence (calm x1.6; off: at once)
    var gone = stub;
    stub = null;
    if (!gone) {
      return;
    }
    gone.el.classList.remove('drawing');
    if (K === 0 || gone.el.classList.contains('away')) {
      gone.el.remove();
      return;
    }
    gone.el.classList.add('leaving');
    setTimeout(function () { gone.el.remove(); }, CUE_FADE_MS * K + 40);
  }

  function showStub(rec, draw) {               // drawn in after the native part (lively), at once otherwise
    if (stub && stub.id !== rec.id) {
      fadeStub();
    }
    if (!stub) {
      var el = make('div', 'cue-stub');
      el.setAttribute('aria-hidden', 'true');
      el.append(make('i', 'cs-line'), make('i', 'cs-dot'));
      document.body.appendChild(el);
      stub = {el: el, id: rec.id};
      el.classList.toggle('drawing', !!draw && motion === 'lively' && K > 0);
    }
    placeStub();
  }

  function placeStub() {                       // from the frame's own place, so it never lags; away while scrolling
    if (!stub) {
      return;
    }
    var rec = frames.get(stub.id);
    var y = rec && litCue && lit === stub.id && !rec.away && !rec.closed && !parked(rec) ? cueY(rec) : null;
    var away = scrolling || y === null || !!big;
    stub.el.classList.toggle('away', away);
    if (away) {
      stub.el.classList.remove('drawing');     // it comes back without the draw-in
      return;
    }
    var dot = Math.max(DOT_GAP, Math.round(rec.el.getBoundingClientRect().left) - DOT_GAP);
    stub.el.classList.toggle('amber', rec.el.classList.contains('waits'));
    stub.el.style.setProperty('--y', y + 'px');
    stub.el.style.setProperty('--dot', dot + 'px');
  }

  // ---- the app's messages -----------------------------------------------------------------------------------------------

  var HANDLERS = Object.create(null);

  HANDLERS.frame = function (message) {
    if (!validFrame(message)) {
      return drop('shape');
    }
    var rec = frames.get(message.id);
    if (!rec && frames.size >= MAX_FRAMES) {
      return drop('full');
    }
    var body = makeBody(message);              // throws on data its kind can't show: nothing changes
    var before = rec ? rec.data : null;
    if (!rec) {
      rec = build(message.id);
      frames.set(message.id, rec);
      placeFrame(rec, message.group || '');
    }
    fill(rec, message, body);
    if (!before) {
      if (!parked(rec)) {
        arrive(rec);                           // a parked answer's frames draw in when it lands
      }
      rec.el.classList.toggle('changed', message.changed);
    } else if (message.changed && (!before.changed || differs(before, message))) {
      lap(rec);
    } else if (!rec.el.classList.contains('lap')) {
      rec.el.classList.toggle('changed', message.changed);
    }
    if (message.closed !== rec.closed) {
      setClosed(rec, message.closed, false);
    }
    if (lit === message.id) {
      rec.el.classList.add('lit');
    }
    if (big && big.id === message.id && big.card && big.state === 'open') {
      fillCard(big.card, rec);                 // the big frame shows what its frame says now
    }
    refresh();
    return true;
  };

  // The frame Jarvis speaks of (part 5c, DESIGN-SPEC 3.4): its brackets close from 10 px out and its ground lights;
  // "cue": the app draws the cue line's native part to it, so the page draws the stub (drawn in for a new lit frame,
  // at once when the cue comes back for the same one). One thing bright at a time: an arriving frame traces in
  // LINE_FOCUS meanwhile.
  HANDLERS.lit = function (message) {
    if (!onlyKeys(message, ['type', 'id', 'cue']) || typeof message.id !== 'string'
        || (message.id !== '' && !ID.test(message.id))
        || (message.cue !== undefined && typeof message.cue !== 'boolean')) {
      return drop('shape');
    }
    var rec = message.id ? frames.get(message.id) || null : null;
    var same = !!rec && lit === message.id;
    if (!same) {
      var old = frames.get(lit);
      if (old) {
        old.el.classList.remove('lit');
      }
      lit = rec ? message.id : '';
      if (rec) {
        rec.el.classList.add('lit');
      }
      root.classList.toggle('has-lit', !!rec);
      fadeStub();
    }
    litCue = !!rec && message.cue === true;
    if (litCue) {
      showStub(rec, !same);
    } else {
      fadeStub();
    }
    return true;
  };

  HANDLERS.remove = function (message) {
    if (!onlyKeys(message, ['type', 'id']) || typeof message.id !== 'string' || !ID.test(message.id)) {
      return drop('shape');
    }
    var rec = frames.get(message.id);
    if (rec) {
      var hadFocus = rec.el.contains(document.activeElement);
      forget(rec, true);
      if (hadFocus) {
        focusBoard();                          // the focus stays on the board (a closed frame's time was up)
      }
    }
    return true;
  };

  HANDLERS.clear = function (message) {
    if (!onlyKeys(message, ['type'])) {
      return drop('shape');
    }
    Array.from(frames.values()).forEach(function (rec) { forget(rec, false); });
    groups.forEach(function (group) {
      group.el.remove();
      group.rule.remove();
    });
    groups.clear();
    lit = '';
    litCue = false;
    fadeStub();
    filter = 'all';
    root.classList.remove('has-lit');
    refresh();
    return true;
  };

  HANDLERS.group = function (message) {
    if (!onlyKeys(message, ['type', 'id', 'time', 'topic', 'frames', 'older', 'parked']) || typeof message.id !== 'string'
        || !ID.test(message.id) || !words(message.time, CAPS.time) || !words(message.topic, CAPS.topic)
        || !list(message.frames, MAX_FRAMES) || typeof message.older !== 'boolean'
        || (message.parked !== undefined && typeof message.parked !== 'boolean')
        || !message.frames.every(function (id) { return typeof id === 'string' && frames.has(id); })) {
      return drop('shape');
    }
    var group = groups.get(message.id);
    var fresh = !group;
    if (!group) {
      if (groups.size >= MAX_GROUPS) {
        return drop('full');
      }
      group = makeGroup(message.id);
      groups.set(message.id, group);
      root.insertBefore(group.el, clearedRow ? clearedRow.el.nextSibling : root.firstChild);   // the newest on top
    }
    var wasParked = group.parked;
    var parkedNow = message.parked === true;
    group.time.textContent = message.time;
    group.topic.textContent = message.topic;
    group.el.classList.toggle('older', message.older);
    group.older = message.older;
    group.parked = parkedNow;
    group.order = message.frames.slice();
    arrange(group);
    if ((fresh && !parkedNow) || (wasParked && !parkedNow)) {
      // A new answer on top (or one that was parked lands): older ones fold again, expanded or not (3.5).
      frames.forEach(function (rec) {
        var owner = groupOf(rec);
        if (owner && owner !== group) {
          rec.expanded = false;
          rec.satButton.setAttribute('aria-expanded', 'false');
        }
      });
    }
    if (wasParked && !parkedNow) {             // it lands: its frames draw in, the board shows it on top
      group.seen = false;
      group.order.forEach(function (id) {
        var rec = frames.get(id);
        if (rec && rec.group === group.id) {
          arrive(rec);
        }
      });
    }
    if ((fresh && !parkedNow) || (wasParked && !parkedNow)) {
      root.scrollTo({top: 0, behavior: K === 0 ? 'auto' : 'smooth'});   // nobody reads: the new answer on top
    }
    refresh();
    return true;
  };

  HANDLERS.remove_group = function (message) {
    if (!onlyKeys(message, ['type', 'id']) || typeof message.id !== 'string' || !ID.test(message.id)) {
      return drop('shape');
    }
    var group = groups.get(message.id);
    if (!group) {
      return true;
    }
    groups.delete(message.id);
    var hadFocus = group.el.contains(document.activeElement);
    Array.from(frames.values()).forEach(function (rec) {     // the whole group leaves, its frames with it
      if (rec.group === group.id) {
        release(rec);
      }
    });
    group.rule.remove();
    var el = group.el;
    el.setAttribute('aria-hidden', 'true');
    el.classList.add('leaving');
    if (K === 0 || !el.animate || group.parked || el.classList.contains('hollow')) {
      el.remove();
    } else {
      after(el.animate([{opacity: 1}, {opacity: 0}], {duration: GROUP_FADE_MS * K, easing: easeOut, fill: 'forwards'}),
            function () { el.remove(); refresh(); });
    }
    refresh();
    if (hadFocus) {
      focusBoard();
    }
    return true;
  };

  function makeBar() {                         // "Board", the counts, All | Pinned (a filter) and Clear (3.1, 3.5)
    var bar = make('header', 'bar');
    bar.id = 'bar';
    var waits = make('span', 'b-waits t-badge');
    var waitsWords = make('span', '');
    waits.append(glyph('diamond', 'b-dia', true), waitsWords);
    var seg = make('div', 'b-seg');
    seg.setAttribute('role', 'group');
    var all = button('on', '', function () { setFilter('all'); });
    all.textContent = WORDS.all;
    var pinned = button('', '', function () { setFilter('pinned'); });
    pinned.textContent = WORDS.pinned;
    var clear = button('b-clear', WORDS.clearAll, function () { post('clear', {}); });
    clear.textContent = WORDS.clear;
    seg.append(all, pinned);
    var count = make('span', 'b-count t-badge');
    bar.append(make('span', 'b-title', WORDS.board), count, waits, seg, clear);
    document.body.insertBefore(bar, root);
    return {bar: bar, count: count, waits: waits, waitsWords: waitsWords, all: all, pinned: pinned, clear: clear};
  }

  HANDLERS.bar = function (message) {
    if (!onlyKeys(message, ['type', 'frames', 'waits', 'pinned', 'compact'])
        || !whole(message.frames, 0, MAX_FRAMES) || !whole(message.waits, 0, message.frames)
        || !whole(message.pinned, 0, message.frames) || typeof message.compact !== 'boolean') {
      return drop('shape');
    }
    if (!barParts) {
      barParts = makeBar();
    }
    document.body.classList.add('has-bar');
    document.body.classList.toggle('compact', message.compact);
    barParts.count.textContent = message.frames + (message.frames === 1 ? WORDS.frame : WORDS.frames);
    barParts.waits.hidden = message.waits === 0;
    barParts.waitsWords.textContent = message.waits + WORDS.waitsCount;
    barParts.pinned.textContent = message.pinned ? WORDS.pinned + ' ' + message.pinned : WORDS.pinned;
    barPinned = message.pinned;
    if (barPinned === 0 && filter === 'pinned') {
      filter = 'all';                          // nothing pinned any more: the whole board again
    }
    measureWidth();
    refresh();
    return true;
  };

  HANDLERS.desk = function (message) {
    if (!onlyKeys(message, ['type', 'state']) || DESK_STATES.indexOf(message.state) < 0) {
      return drop('shape');
    }
    var html = document.documentElement;
    html.classList.remove('desk-in');
    if (message.state === 'enter') {
      void html.getBoundingClientRect();         // the fade and slide start again
      html.classList.add('desk-in');
    }
    measureWidth();
    scheduleView();
    scheduleLayout();
    return true;
  };

  HANDLERS.reveal = function (message) {
    if (!onlyKeys(message, ['type', 'group']) || typeof message.group !== 'string' || !ID.test(message.group)) {
      return drop('shape');
    }
    var group = groups.get(message.group);
    if (!group) {
      return drop('id');
    }
    root.scrollTo({top: Math.max(0, group.el.offsetTop - 6), behavior: K === 0 ? 'auto' : 'smooth'});
    return true;
  };

  HANDLERS.cleared = function (message) {     // what the bar's Clear took away, out of sight; [] none
    if (!onlyKeys(message, ['type', 'frames']) || !knownIds(message.frames, MAX_FRAMES)) {
      return drop('shape');
    }
    var gone = new Set(message.frames);
    var hadFocus = null;
    frames.forEach(function (rec) {
      if (gone.has(rec.id) && rec.el.contains(document.activeElement)) {
        hadFocus = rec;
      }
      rec.cleared = gone.has(rec.id);
    });
    refresh();
    if (hadFocus && clearedRow) {
      clearedRow.back.focus();                 // the focus stays on the board: on Bring back
    }
    return true;
  };

  function focusBoard() {                      // the keyboard focus to the frame in view (else the first one)
    var shown = boardFrames();
    var box = root.getBoundingClientRect();
    var pick = null;
    shown.forEach(function (rec) {
      var place = rec.el.getBoundingClientRect();
      if (!pick && place.height > 0 && place.bottom > box.top + IN_VIEW_TOP && place.top < box.bottom - IN_VIEW_END) {
        pick = rec;
      }
    });
    pick = pick || shown[0] || null;
    if (pick) {
      target(pick).focus({preventScroll: true});
      target(pick).scrollIntoView({block: 'nearest'});
      return true;
    }
    if (clearedRow && clearedRow.el.classList.contains('shown')) {
      clearedRow.back.focus();
      return true;
    }
    if (stripParts && stripParts.pill.classList.contains('shown')) {
      stripParts.pillButton.focus();
      return true;
    }
    return false;
  }

  function focusStrip() {
    if (!stripParts) {
      return false;
    }
    updateView();
    var stop = stripParts.roving;
    if (!stop) {
      return false;
    }
    stop.focus();
    return true;
  }

  HANDLERS.focus_part = function (message) {   // F6's cycle: the app moved the window's focus into the page
    if (!onlyKeys(message, ['type', 'part']) || FOCUS_PARTS.indexOf(message.part) < 0) {
      return drop('shape');
    }
    document.documentElement.classList.add('keys');
    if (big && big.card) {
      big.card.focus({preventScroll: true});   // open big: the keyboard is on the big frame (Esc goes back)
      return true;
    }
    var inStrip = !!stripParts && stripParts.nav.contains(document.activeElement);
    var part = message.part;
    if (part === 'next') {
      part = inStrip ? 'ask' : 'strip';
    } else if (part === 'prev') {
      part = inStrip ? 'board' : 'conversation';
    }
    if (part === 'strip' && !focusStrip()) {
      part = message.part === 'next' ? 'ask' : 'board';
    }
    if (part === 'board' && !focusBoard()) {
      part = message.part === 'prev' ? 'conversation' : 'ask';
    }
    if (part === 'ask' || part === 'conversation') {
      post('focus_out', {to: part});
    }
    return true;
  };

  HANDLERS.motion = function (message) {
    if (!onlyKeys(message, ['type', 'level']) || typeof message.level !== 'string'
        || !Object.prototype.hasOwnProperty.call(MOTIONS, message.level)) {
      return drop('shape');
    }
    setMotion(message.level);
    return true;
  };

  HANDLERS.picture = function (message) {
    if (!onlyKeys(message, ['type', 'id', 'src']) || typeof message.id !== 'string' || !ID.test(message.id)) {
      return drop('shape');
    }
    var src = message.src;
    if (typeof src !== 'string' || src.length > PICTURE_CAP || src.indexOf(PICTURE_PREFIX) !== 0
        || !BASE64.test(src.slice(PICTURE_PREFIX.length))) {
      return drop('picture');
    }
    var rec = frames.get(message.id);
    if (!rec) {
      return drop('id');
    }
    var picture = make('img', 'f-pic');
    picture.alt = '';
    picture.decoding = 'async';
    picture.draggable = false;                 // a picture only: never dragged out, never a link
    picture.src = src;                         // a data: picture the app re-encoded; nothing else loads (img-src data:)
    picture.addEventListener('load', function () {
      scheduleView();
      scheduleLayout();                        // its height came with it: the frames below moved
    });
    if (rec.picture) {
      rec.picture.remove();
    }
    rec.picture = picture;                     // one picture per frame: a new one replaces it
    placePicture(rec);
    if (big && big.id === message.id && big.card && big.state === 'open') {
      fillCard(big.card, rec);                 // the big frame shows its frame's new picture too
    }
    scheduleView();
    scheduleLayout();
    return true;
  };

  // ---- open big (part 5c, DESIGN-SPEC 3.5): the view is wide for a moment, the frame grows to the whole of it ---------

  function jpegUrl(src) {                      // ONE JPEG data: URL within the picture's caps (as Python checks it)
    return typeof src === 'string' && src.length <= PICTURE_CAP && src.indexOf(PICTURE_PREFIX) === 0
      && src.length > PICTURE_PREFIX.length && BASE64.test(src.slice(PICTURE_PREFIX.length));
  }

  HANDLERS.backdrop = function (message) {     // the conversation column, painted where it was while the view is wide
    if (!onlyKeys(message, ['type', 'src'])) {
      return drop('shape');
    }
    if (!jpegUrl(message.src)) {
      return drop('picture');
    }
    if (!backdrop) {
      backdrop = make('img', '');
      backdrop.id = 'backdrop';
      backdrop.alt = '';
      backdrop.draggable = false;
      backdrop.setAttribute('aria-hidden', 'true');
      backdrop.addEventListener('click', function () { shrink(true); });
      document.body.insertBefore(backdrop, document.body.firstChild);
    }
    backdrop.src = message.src;                // a data: picture the app made; nothing else loads (img-src data:)
    return true;
  };

  HANDLERS.big = function (message) {
    var state = message ? message.state : '';
    if (state === 'open') {
      if (!onlyKeys(message, ['type', 'state', 'id', 'x', 'y', 'w', 'h']) || typeof message.id !== 'string'
          || !ID.test(message.id) || !whole(message.x, 0, BIG_LIMIT) || !whole(message.y, 0, BIG_LIMIT)
          || !whole(message.w, 1, BIG_LIMIT) || !whole(message.h, 1, BIG_LIMIT)) {
        return drop('shape');
      }
      var rec = frames.get(message.id);
      if (!rec) {
        return drop('id');
      }
      var box = {x: message.x, y: message.y, w: message.w, h: message.h};
      if (big && big.id === message.id && (big.state === 'wait' || big.state === 'open')) {
        big.box = box;                         // the window was resized while big: the same frame, a new box
        placeBig();
        return true;
      }
      if (big) {
        endBig();
      }
      big = {id: message.id, rec: rec, card: null, state: 'wait', box: box};
      measureWidth(box.w);                     // the board's own width, as it was before the view went wide (a resize
      placeBig();                              // that came first measured the wide view)
      return true;
    }
    if (!onlyKeys(message, ['type', 'state', 'id']) || state !== 'close' || typeof message.id !== 'string'
        || (message.id !== '' && !ID.test(message.id))) {
      return drop('shape');
    }
    shrink(false);                             // the app's own reason (its frame left, the desk went): back
    return true;
  };

  function isWide() {                          // the view is as wide as the app made it: the board's box ends at its edge
    return !!big && big.box.x > 0 && Math.abs(window.innerWidth - (big.box.x + big.box.w)) <= 2;
  }

  function laidOut() {                         // the page shows itself at the view's new size: the app's still goes
    var mine = ++bigTurn;                      // (only while this step is the latest: never a "laid" for an open it
    requestAnimationFrame(function () {        // left, or for a close an open asked after)
      requestAnimationFrame(function () {
        if (mine === bigTurn) {
          post('big', {step: 'laid'});
        }
      });
    });
  }

  function askOpen(id) {                       // Enter, a double click, the Open big button: the app opens it big
    bigTurn += 1;
    post('open', {id: id});
  }

  function underBig(on) {                      // while a frame is big the board under it takes no key, no click and
    [barParts && barParts.bar, root, stripParts && stripParts.nav, stripParts && stripParts.more,   // no focus
     stripParts && stripParts.pill].forEach(function (node) {
      if (node) {
        node.inert = !!on;
      }
    });
  }

  function cardTab(back) {                     // Tab in open big: the big frame, then its own controls, round
    if (!big || !big.card) {
      return;
    }
    var stops = [big.card].concat(Array.prototype.filter.call(big.card.querySelectorAll('button'), function (node) {
      return !node.disabled;
    }));
    var at = stops.indexOf(document.activeElement);
    var next = at < 0 ? stops[back ? stops.length - 1 : 0] : stops[(at + (back ? stops.length - 1 : 1)) % stops.length];
    next.focus({preventScroll: true});
  }

  function placeBig() {                        // on each big message and resize: the board keeps its own box
    var body = document.body;
    var wide = isWide();
    if (wide) {
      body.style.setProperty('--bx', big.box.x + 'px');
      body.style.setProperty('--by', big.box.y + 'px');
      body.style.setProperty('--bw', big.box.w + 'px');
      body.style.setProperty('--bh', big.box.h + 'px');
    }
    body.classList.toggle('wide', wide);
    if (big && big.state === 'wait' && wide) {
      big.state = 'open';
      grow();
    } else if (big && big.state === 'shut' && !wide) {
      big = null;                              // the app set the geometry back: the board is its own again, measured
      measureWidth();                          // at its real width (it re-flows only when that changed meanwhile)
      laidOut();
    }
    placeStub();
  }

  function fillCard(card, rec) {               // the big frame: the frame's words at the whole view's size
    var data = rec.data;
    var body;
    try {
      body = makeBody(data);
    } catch (error) {
      return;
    }
    var head = make('div', 'f-head');
    var title = make('h2', 'f-title t-frame-title', data.title);
    title.id = 'big-title';
    head.append(glyph(GLYPHS[data.kind] || 'read-all', 'f-glyph'), title,
                make('span', 'f-meta t-badge', data.meta ? data.meta + ' · ' + WORDS.openBigMeta : WORDS.openBigMeta));
    var parts = [head, body];
    if (rec.picture) {
      var picture = make('img', 'f-pic');
      picture.alt = '';
      picture.draggable = false;
      picture.src = rec.picture.src;          // the frame's own data: picture again
      var slot = body.querySelector('.pic-slot');
      if (slot) {
        slot.appendChild(picture);
      } else {
        parts.push(picture);
      }
    }
    if (data.waits) {
      var chip = button('f-wait', '', function () { post('go_approvals', {id: rec.id}); });
      chip.addEventListener('mousedown', function (event) { event.preventDefault(); });
      chip.append(glyph('diamond', 'f-dia', true), make('span', 'f-words', data.waits), glyph('chevron', 'f-chev'),
                  make('span', 'f-key t-kbd', WORDS.approvalsKey));
      parts.push(chip);
    }
    parts.push(make('div', 'f-back t-badge', WORDS.backToBoard));
    card.className = 'frame big-card' + (data.waits ? ' waits' : '') + (data.status === 'failed' ? ' failed' : '')
      + (data.status === 'empty' ? ' empty' : '');
    var inside = card.contains(document.activeElement) && document.activeElement !== card;
    card.replaceChildren.apply(card, parts);
    if (inside) {
      card.focus({preventScroll: true});       // a control of it had the focus: it stays in the big frame
    }
  }

  function grow() {                            // the frame grows from its rectangle to the whole view (260 ms expo-out)
    var rec = big.rec;
    if (!bigLayer) {
      bigLayer = make('div', '');
      bigLayer.id = 'big';
      var scrim = make('div', 'big-scrim');
      scrim.setAttribute('aria-hidden', 'true');   // a click beside the big frame goes back (Esc says so for keys)
      scrim.addEventListener('click', function () { shrink(true); });
      bigLayer.appendChild(scrim);
    }
    var card = make('article', 'frame big-card');
    card.tabIndex = -1;
    card.setAttribute('aria-labelledby', 'big-title');
    card.addEventListener('mousedown', function (event) {
      if (event.detail > 1) {
        event.preventDefault();
      }
    });
    card.addEventListener('dblclick', function (event) {
      if (!(event.target.closest && event.target.closest('button'))) {
        shrink(true);
      }
    });
    fillCard(card, rec);
    big.card = card;
    var mine = big;
    bigLayer.appendChild(card);
    document.body.appendChild(bigLayer);
    document.body.classList.add('dim');        // the backdrop and the other frames go to 20 %
    underBig(true);
    card.focus({preventScroll: true});         // the focus goes with it (the frame under it is inert now)
    var from = rec.el.getBoundingClientRect();
    var start = function () {
      if (big !== mine || mine.state !== 'open' || mine.card !== card) {
        return;                                // gone back (or another frame big) before its picture was ready
      }
      laidOut();
      card.focus({preventScroll: true});
      if (K === 0 || !card.animate || from.width < 1 || from.height < 1) {
        return;                                // motion off: there at once
      }
      var width = window.innerWidth, height = window.innerHeight;
      card.animate([
        {transform: 'translate(' + from.left + 'px, ' + from.top + 'px) scale(' + (from.width / width) + ', '
          + (from.height / height) + ')', opacity: 0.35},
        {opacity: 1, offset: 0.4},
        {transform: 'none', opacity: 1}
      ], {duration: SCALE_MS * K, easing: easeOut});
    };
    var decoded = backdrop && backdrop.decode ? backdrop.decode() : Promise.resolve();
    decoded.then(start, start);
  }

  function shrink(byOwner) {                   // back to the board: the scale-down (220 ms), then the app's geometry
    if (!big || big.state === 'closing' || big.state === 'shut') {
      return;
    }
    var card = big.card, rec = big.rec, mine = big;
    big.state = 'closing';
    bigTurn += 1;                              // its open's "laid", if still to come, is never sent now
    if (byOwner) {
      post('big', {step: 'back'});
    }
    document.body.classList.remove('dim');
    var hadFocus = !!card && card.contains(document.activeElement);
    var done = function () {
      if (big !== mine) {
        return;
      }
      if (card) {
        card.remove();
      }
      if (bigLayer) {
        bigLayer.remove();                     // its scrim never stays over the board
      }
      underBig(false);
      mine.card = null;
      mine.state = 'shut';
      if (hadFocus && current(rec)) {
        target(rec).focus({preventScroll: true});
      }
      post('big', {step: 'shut'});
      if (!isWide()) {
        placeBig();                            // the view was never wide (or is narrow again already)
      }
    };
    if (!card || K === 0 || !card.animate || !current(rec) || rec.away || rec.closed) {
      done();
      return;
    }
    var to = rec.el.getBoundingClientRect();
    var width = window.innerWidth, height = window.innerHeight;
    after(card.animate([
      {transform: 'none', opacity: 1},
      {opacity: 1, offset: 0.6},
      {transform: 'translate(' + to.left + 'px, ' + to.top + 'px) scale(' + (to.width / width) + ', '
        + (to.height / height) + ')', opacity: 0}
    ], {duration: UNSCALE_MS * K, easing: easeOut, fill: 'forwards'}), done);
  }

  function endBig() {                          // gone at once (another frame takes its place)
    if (!big) {
      return;
    }
    if (big.card) {
      big.card.remove();
    }
    if (bigLayer) {
      bigLayer.remove();
    }
    document.body.classList.remove('dim');
    underBig(false);
    big = null;
  }

  function apply(text) {
    try {
      if (typeof text !== 'string' || text.length > MESSAGE_CAP + PICTURE_CAP) {
        return drop('size');
      }
      var message = JSON.parse(text);
      if (!message || typeof message !== 'object' || Array.isArray(message) || typeof message.type !== 'string') {
        return drop('shape');
      }
      var handle = HANDLERS[message.type];
      if (!handle) {
        return drop('type');
      }
      var picture = (message.type === 'picture' || message.type === 'backdrop') && typeof message.src === 'string'
        ? message.src.length : 0;
      if (text.length - picture > MESSAGE_CAP) {
        return drop('size');
      }
      return handle(message) !== false;
    } catch (error) {
      return drop('error');
    }
  }

  // ---- start ----------------------------------------------------------------------------------------------------------

  function fontsLoaded(done) {                 // the frames' text is measured in the right faces from the first one
    var finished = false;
    function finish() {
      if (!finished) {
        finished = true;
        done();
      }
    }
    setTimeout(finish, FONT_WAIT_MS);
    try {
      var display = token('--font-display'), body = token('--font-body'), mono = token('--font-mono');
      Promise.all(['500 15px ' + display, '600 15px ' + display, '400 13px ' + body, '500 10px ' + mono]
        .map(function (spec) { return document.fonts.load(spec); })).then(finish, finish);
    } catch (ignored) {
      finish();
    }
  }

  function connect() {
    var transport = window.qt && window.qt.webChannelTransport;
    if (typeof QWebChannel !== 'function' || !transport) {
      return;                                  // no channel: the app's time limit says the board can't start
    }
    new QWebChannel(transport, function (channel) {
      bridge = channel.objects.bridge || null;
      fontsLoaded(function () {
        if (!readySent) {
          readySent = true;
          post('ready', {v: 1});
        }
      });
    });
  }

  function start() {
    root = document.getElementById('board');
    if (!root) {
      return;
    }
    clearedRow = makeCleared();
    root.appendChild(clearedRow.el);
    stripParts = makeStrip();
    document.addEventListener('keydown', onKey);
    root.addEventListener('focusin', onFocusIn);
    root.addEventListener('focusout', onFocusOut);
    root.addEventListener('scroll', onScroll, {passive: true});
    document.addEventListener('dragstart', function (event) { event.preventDefault(); });   // nothing leaves the page
    window.addEventListener('resize', function () {
      placeBig();                              // first: the board keeps its own box in a wide view (open big)
      measureWidth();
      scheduleView();
      scheduleLayout();
    });
    watchReading();
    easeOut = token('--ease-out') || easeOut;
    setMotion(motion);
    connect();
  }

  Object.defineProperty(window, 'board', {
    value: Object.freeze({
      apply: apply,
      counts: function () {
        return Object.assign({frames: frames.size, groups: groups.size, big: big ? big.state : '', lit: lit}, dropped);
      }
    }),
    writable: false, configurable: false, enumerable: false
  });
  start();
}());
