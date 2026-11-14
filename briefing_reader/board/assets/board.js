/* The board's page (the new look, part 3): Jarvis's own code, no library. Every element is made with createElement,
   every word is set with textContent and every listener with addEventListener: no markup is ever read, no address is
   ever opened. The app sends board.apply(<JSON text>), checked in Python first and checked here again; the page answers
   on its web channel with bridge.post(kind, <JSON text>), the short list Python accepts (ready, layout, focus, open,
   close, pin, bring_back, retry, go_approvals). DESIGN-SPEC 3.2 (the frame), 3.4 (its states) and 5 (motion). */
(function () {
  'use strict';

  var MESSAGE_CAP = 32768;                     // bytes of one message's JSON (board.protocol.MESSAGE_CAP) ...
  var PICTURE_CAP = 419840;                    // ... a picture message's one data: URL apart (PICTURE_CAP)
  var PICTURE_PREFIX = 'data:image/jpeg;base64,';
  var BASE64 = /^[A-Za-z0-9+\/]*={0,2}$/;
  var ID = /^[A-Za-z0-9_-]{1,40}$/;
  var CAPS = {title: 60, meta: 40, text: 400, status_text: 160, status_meta: 40, waits: 60};
  var FRAME_FIELDS = ['type', 'id', 'kind', 'title', 'meta', 'body', 'status', 'status_text', 'status_meta', 'waits',
                      'changed', 'pinned', 'closed'];
  var STATUSES = ['ok', 'failed', 'empty'];
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
    approvalsKey: 'Ctrl 3', pin: 'Pin', unpin: 'Unpin', openBig: 'Open big', close: 'Close'
  };
  var GLYPHS = {text: 'read-all'};             // each kind's glyph (holo's icons, drawn by tokens.css)

  // The renderer table: one function per kind. It fills the frame's body from the frame's own data and throws on data
  // it can't show (the message is then dropped and nothing changes). Part 4 adds the real kinds here.
  var KINDS = Object.create(null);
  KINDS.text = function (body, frame) {
    if (!onlyKeys(frame.body, ['text']) || !words(frame.body.text, CAPS.text)) {
      throw new TypeError('text');
    }
    if (frame.body.text) {
      body.appendChild(make('p', 'f-text t-body-sm', frame.body.text));
    }
  };

  var root = null;
  var bridge = null;
  var readySent = false;
  var frames = new Map();                      // id -> the frame's record (in arrival order)
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
               closedTimer: 0, drawTimer: 0, lapTimer: 0};
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

    rec.wait = button('f-wait', '', function () { post('go_approvals', {id: id}); });
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
      && !!message.body && typeof message.body === 'object' && !Array.isArray(message.body);
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

  function forget(rec, animate) {              // the frame leaves the board
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
      var list = Array.prototype.filter.call(root.children, function (node) {
        var other = records.get(node);
        return !!other && !other.closed && frames.get(other.id) === other;
      });
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
      root.appendChild(rec.el);
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
    lit = '';
    root.classList.remove('has-lit');
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
    picture.src = src;                         // a data: picture the app re-encoded; nothing else loads (img-src data:)
    if (rec.picture) {
      rec.picture.replaceWith(picture);
    } else {
      rec.body.after(picture);
    }
    rec.picture = picture;
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
    window.addEventListener('resize', scheduleLayout);
    easeOut = token('--ease-out') || easeOut;
    setMotion(motion);
    connect();
  }

  Object.defineProperty(window, 'board', {
    value: Object.freeze({
      apply: apply,
      counts: function () { return Object.assign({frames: frames.size}, dropped); }
    }),
    writable: false, configurable: false, enumerable: false
  });
  start();
}());
