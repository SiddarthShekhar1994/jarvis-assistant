/* The board's page (the new look, parts 3, 4 and 5a): Jarvis's own code, no library. Every element is made with
   createElement, every word is set with textContent and every listener with addEventListener: no markup is ever read,
   no address is ever opened. The app sends board.apply(<JSON text>), checked in Python first and checked here again;
   the page answers on its web channel with bridge.post(kind, <JSON text>), the short list Python accepts (ready, layout,
   focus, open, close, pin, bring_back, retry, go_approvals). DESIGN-SPEC 3.1 (the bar and the groups), 3.2 (the
   frame), 3.3 (the kinds), 3.4 (its states) and 5 (motion). */
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
                      'changed', 'pinned', 'closed', 'group'];
  var MAX_GROUPS = 8;
  var DESK_STATES = ['enter', 'leave'];
  var GROUP_FADE_MS = 160;                     // a group that leaves for room fades, then goes
  var STATUSES = ['ok', 'failed', 'empty'];
  var CALENDAR_STATES = ['', 'past', 'now', 'next', 'free', 'new', 'struck', 'today'];
  var SPAN_KINDS = ['busy', 'next', 'free'];
  var TODO_STATES = ['open', 'waits', 'done'];
  var MOTIONS = {lively: 1, calm: 1.6, off: 0};
  var MAX_FRAMES = 24;
  var MAX_ANIMATED = 6;                        // frames of one arrival that draw in; the rest are there at once
  var STAGGER_MS = 80, DRAW_MS = 800, LAP_MS = 900, FADE_MS = 160, HEIGHT_MS = 200;
  var CLOSED_MS = 8000;                        // a closed frame's row offers Bring back this long
  var LAYOUT_MS = 100;                         // at most 10 layouts a second ...
  var SCROLL_END_MS = 150;                     // ... and one more this long after a scroll stops
  var BATCH_MS = 300, FONT_WAIT_MS = 1500;
  var SVG = 'http://www.w3.org/2000/svg';
  var WORDS = {
    changed: 'Changed', tryAgain: 'Try again', bringBack: 'Bring back', closed: 'Closed: ',
    keysFirst: 'Enter open big · P pin', keysSecond: 'Del close · Esc ask bar', dot: '·',
    approvalsKey: 'Ctrl 3', pin: 'Pin', unpin: 'Unpin', openBig: 'Open big', close: 'Close',
    waits: 'Waits', done: 'Done', pictureOnly: 'Picture only', unread: 'Unread',
    board: 'Board', frame: ' frame', frames: ' frames', waitsCount: ' waits', all: 'All', pinned: 'Pinned',
    clear: 'Clear', show: 'Show'
  };
  // Each kind's glyph (holo's icons, drawn by tokens.css).
  var GLYPHS = {text: 'read-all', calendar: 'calendar', mail_thread: 'mail', mail_list: 'inbox', todos: 'todos',
                picture: 'image'};

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
  var records = new WeakMap();                 // element -> record
  var lit = '';
  var focused = '';
  var motion = 'lively';
  var K = 1;                                   // the motion's multiplier, as --k in board.css
  var easeOut = 'cubic-bezier(0.16, 1, 0.3, 1)';
  var batch = 0, batchTimer = 0;
  var layoutTimer = 0, lastLayout = 0, scrolling = false, scrollTimer = 0;
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
      press();
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

  // ---- a frame ------------------------------------------------------------------------------------------------------

  function build(id) {
    var art = make('article', 'frame');
    var rec = {id: id, el: art, kind: '', data: null, pinned: false, closed: false, picture: null,
               closedTimer: 0, drawTimer: 0, lapTimer: 0, group: ''};
    records.set(art, rec);
    art.tabIndex = 0;
    art.setAttribute('aria-labelledby', 'title-' + id);
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
    var controls = make('div', 'f-ctl');
    rec.pinButton = button('f-icon', WORDS.pin, function () { togglePin(rec); });
    rec.pinButton.appendChild(glyph('pin'));
    var openBig = button('f-icon', WORDS.openBig, function () { post('open', {id: id}); });
    openBig.appendChild(glyph('expand'));
    var closer = button('f-icon', WORDS.close, function () { close(rec); });
    closer.appendChild(glyph('close'));
    controls.append(rec.pinButton, openBig, closer);
    head.append(rec.glyph, rec.heading, rec.tag, rec.pinMark, rec.meta, controls);
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
      var mark = glyph(GLYPHS[message.kind] || 'read-all', 'f-glyph');
      rec.glyph.replaceWith(mark);
      rec.glyph = mark;
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
    updatePinned(rec, message.pinned);
    rec.data = message;
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
                 list: make('div', 'g-frames'), order: []};
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

  function boardFrames() {                     // the frames in board order (every group's, top to bottom)
    return Array.prototype.filter.call(root.querySelectorAll('.frame'), function (node) {
      var rec = records.get(node);
      return !!rec && !rec.closed && frames.get(rec.id) === rec;
    });
  }

  // ---- motion -------------------------------------------------------------------------------------------------------

  function setMotion(level) {
    motion = level;
    var html = document.documentElement;
    html.classList.remove('motion-lively', 'motion-calm', 'motion-off');
    html.classList.add('motion-' + level);
    K = reducedMotion() ? 0 : MOTIONS[level];
  }

  function arrive(rec) {                       // the outline traces, the ticks snap on, the content comes in
    var index = batch;
    batch += 1;
    clearTimeout(batchTimer);
    batchTimer = setTimeout(function () { batch = 0; }, BATCH_MS);
    if (K === 0 || index >= MAX_ANIMATED) {
      return;                                  // filled at once
    }
    rec.el.style.setProperty('--i', String(index));
    rec.el.classList.add('arriving');
    clearTimeout(rec.drawTimer);
    rec.drawTimer = setTimeout(function () {
      rec.el.classList.remove('arriving');
      rec.el.style.removeProperty('--i');
      scheduleLayout();
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

  function resize(el, from, done) {            // close / bring back: fade 160 ms, height 200 ms
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

  function togglePin(rec) {
    updatePinned(rec, !rec.pinned);
    post('pin', {id: rec.id, on: rec.pinned});
  }

  function setClosed(rec, closed, byOwner) {
    var el = rec.el;
    var from = el.getBoundingClientRect().height;
    var hadFocus = el.contains(document.activeElement);
    clearTimeout(rec.closedTimer);
    rec.closed = closed;
    el.classList.toggle('closed', closed);
    el.tabIndex = closed ? -1 : 0;
    if (closed) {
      rec.closedTimer = setTimeout(function () { forget(rec, true); }, CLOSED_MS);
      if (hadFocus) {
        rec.bringBack.focus();
      }
    } else if (byOwner) {
      el.focus();
    }
    if (byOwner) {
      resize(el, from);
    }
    scheduleLayout();
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

  function release(rec) {                      // the frame is no longer the board's (its element may stay a moment)
    if (frames.get(rec.id) === rec) {
      frames.delete(rec.id);
    }
    clearTimeout(rec.closedTimer);
    clearTimeout(rec.drawTimer);
    clearTimeout(rec.lapTimer);
    if (lit === rec.id) {
      lit = '';
      root.classList.remove('has-lit');
    }
    if (focused === rec.id) {
      focused = '';
    }
  }

  function forget(rec, animate) {              // the frame leaves the board
    release(rec);
    var el = rec.el;
    if (!animate || K === 0 || !el.animate) {
      el.remove();
      scheduleLayout();
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
                    scheduleLayout();
                  });
          });
  }

  function onKey(event) {
    if (event.ctrlKey || event.altKey || event.metaKey) {
      return;
    }
    var rec = records.get(event.target);
    if (!rec || rec.closed) {
      return;
    }
    var key = event.key;
    if (key === 'Enter') {
      post('open', {id: rec.id});
    } else if (key === 'p' || key === 'P') {
      togglePin(rec);
    } else if (key === 'Delete') {
      close(rec);
    } else if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'Home' || key === 'End') {
      var list = boardFrames();
      var at = list.indexOf(rec.el);
      var next = key === 'Home' ? list[0] : key === 'End' ? list[list.length - 1]
        : list[at + (key === 'ArrowDown' ? 1 : -1)];
      if (next) {
        next.focus();
        next.scrollIntoView({block: 'nearest'});
      }
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
    }
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(function () {
      scrolling = false;
      scheduleLayout();
    }, SCROLL_END_MS);
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
      arrive(rec);
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
    scheduleLayout();
    return true;
  };

  HANDLERS.lit = function (message) {
    if (!onlyKeys(message, ['type', 'id']) || typeof message.id !== 'string'
        || (message.id !== '' && !ID.test(message.id))) {
      return drop('shape');
    }
    var old = frames.get(lit);
    if (old) {
      old.el.classList.remove('lit');
    }
    var rec = message.id ? frames.get(message.id) : null;
    lit = rec ? message.id : '';
    if (rec) {
      rec.el.classList.add('lit');
    }
    root.classList.toggle('has-lit', !!rec);
    return true;
  };

  HANDLERS.remove = function (message) {
    if (!onlyKeys(message, ['type', 'id']) || typeof message.id !== 'string' || !ID.test(message.id)) {
      return drop('shape');
    }
    var rec = frames.get(message.id);
    if (rec) {
      forget(rec, true);
    }
    return true;
  };

  HANDLERS.clear = function (message) {
    if (!onlyKeys(message, ['type'])) {
      return drop('shape');
    }
    Array.from(frames.values()).forEach(function (rec) { forget(rec, false); });
    groups.forEach(function (group) { group.el.remove(); });
    groups.clear();
    lit = '';
    root.classList.remove('has-lit');
    return true;
  };

  HANDLERS.group = function (message) {
    if (!onlyKeys(message, ['type', 'id', 'time', 'topic', 'frames', 'older']) || typeof message.id !== 'string'
        || !ID.test(message.id) || !words(message.time, CAPS.time) || !words(message.topic, CAPS.topic)
        || !list(message.frames, MAX_FRAMES) || typeof message.older !== 'boolean'
        || !message.frames.every(function (id) { return typeof id === 'string' && frames.has(id); })) {
      return drop('shape');
    }
    var group = groups.get(message.id);
    if (!group) {
      if (groups.size >= MAX_GROUPS) {
        return drop('full');
      }
      group = makeGroup(message.id);
      groups.set(message.id, group);
      root.insertBefore(group.el, root.firstChild);     // the newest group on top
    }
    group.time.textContent = message.time;
    group.topic.textContent = message.topic;
    group.el.classList.toggle('older', message.older);
    group.order = message.frames.slice();
    arrange(group);
    scheduleLayout();
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
    Array.from(frames.values()).forEach(function (rec) {     // the whole group leaves, its frames with it
      if (rec.group === group.id) {
        release(rec);
      }
    });
    var el = group.el;
    el.setAttribute('aria-hidden', 'true');
    el.classList.add('leaving');
    if (K === 0 || !el.animate) {
      el.remove();
    } else {
      after(el.animate([{opacity: 1}, {opacity: 0}], {duration: GROUP_FADE_MS * K, easing: easeOut, fill: 'forwards'}),
            function () { el.remove(); scheduleLayout(); });
    }
    scheduleLayout();
    return true;
  };

  function makeBar() {                         // "Board", the counts, and All | Pinned and Clear (5b makes them work)
    var bar = make('header', 'bar');
    bar.id = 'bar';
    var waits = make('span', 'b-waits t-badge');
    var waitsWords = make('span', '');
    waits.append(glyph('diamond', 'b-dia', true), waitsWords);
    var seg = make('div', 'b-seg');
    seg.setAttribute('role', 'group');
    var all = make('button', 'on', WORDS.all);
    var pinned = make('button', '', WORDS.pinned);
    var clear = make('button', 'b-clear', WORDS.clear);
    [all, pinned, clear].forEach(function (node) {   // drawn, not working yet: never the focus, never a click
      node.type = 'button';
      node.tabIndex = -1;
      node.setAttribute('aria-disabled', 'true');
    });
    seg.append(all, pinned);
    var count = make('span', 'b-count t-badge');
    bar.append(make('span', 'b-title', WORDS.board), count, waits, seg, clear);
    document.body.insertBefore(bar, root);
    return {bar: bar, count: count, waits: waits, waitsWords: waitsWords, pinned: pinned};
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
    scheduleLayout();
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
    if (rec.picture) {
      rec.picture.remove();
    }
    rec.picture = picture;                     // one picture per frame: a new one replaces it
    placePicture(rec);
    scheduleLayout();
    return true;
  };

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
      var picture = message.type === 'picture' && typeof message.src === 'string' ? message.src.length : 0;
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
    root.addEventListener('keydown', onKey);
    root.addEventListener('focusin', onFocusIn);
    root.addEventListener('focusout', onFocusOut);
    root.addEventListener('scroll', onScroll, {passive: true});
    document.addEventListener('dragstart', function (event) { event.preventDefault(); });   // nothing leaves the page
    window.addEventListener('resize', scheduleLayout);
    easeOut = token('--ease-out') || easeOut;
    setMotion(motion);
    connect();
  }

  Object.defineProperty(window, 'board', {
    value: Object.freeze({
      apply: apply,
      counts: function () { return Object.assign({frames: frames.size, groups: groups.size}, dropped); }
    }),
    writable: false, configurable: false, enumerable: false
  });
  start();
}());
