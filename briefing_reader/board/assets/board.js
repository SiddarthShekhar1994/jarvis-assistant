/* The board's page (the new look, parts 3, 4, 5a, 5b and 5c): Jarvis's own code, no library. Every element is made with
   createElement, every word is set with textContent and every listener with addEventListener: no markup is ever read,
   no address is ever opened. The app sends board.apply(<JSON text>), checked in Python first and checked here again;
   the page answers on its web channel with bridge.post(kind, <JSON text>), the short list Python accepts (ready, layout,
   focus, open, close, pin, bring_back, retry, go_approvals, clear, restore, show_new, reading, focus_out, landed, big,
   go). The canvas (PLAN-canvas part 1): the same page is Home itself - the orb and the corners' controls under the
   board (makeCanvas, at the first orb or canvas message; a board beside a conversation has none of it). Part 2: on
   the canvas the frames are laid out over the whole screen on a 12-column grid (packCanvas), in the mock's glowing
   style, with the task card, the boot of an arriving frame, Tidy and Clear view's lines into the orb.
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
  var FOCUS_PARTS = ['board', 'strip', 'next', 'prev', 'controls'];
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
  // Part 6 fixes: open big steps a table's and maths' sizes down this many times (board.css fit-1 to fit-3, the last
  // the board's own) before its body scrolls; a key scrolls it a line of this many px (a page: its height less a line).
  var FIT_STEPS = 3, FIT_KINDS = ['table', 'maths'], LINE_PX = 40;
  var LANDED_STEPS = ['outline', 'done'];
  // Part 6, Ask's typed frames (board.protocol's caps; board.ask_frames made each one from the model's own, checked):
  // a table 6 x 12, a chart's 8 bars, a timeline's 8 nodes, maths' 8 steps and its tree (6 levels, 64 nodes a frame).
  var TABLE = {columns: 6, rows: 12, column: 24, cell: 40, unit: 12, change: 80, source: 60};
  var CHART = {bars: 8, label: 24, text: 24};
  var TIMELINE = {nodes: 8, when: 24, words: 40};
  var MATHS = {steps: 8, name: 24, depth: 6, nodes: 64, text: 24};
  var NUMBER_LIMIT = 1e15;                     // a number on the board is under this (and finite)
  var NODE_STATES = ['done', 'next', 'due', ''];
  var MATHS_OPS = Object.create(null);         // an op and how many arguments it takes (never a prototype's key)
  [['+', 2, 8], ['-', 1, 2], ['*', 2, 8], ['/', 2, 2], ['=', 2, 4], ['frac', 2, 2], ['pow', 2, 2], ['root', 1, 2],
   ['()', 1, 1], ['or', 2, 4]].forEach(function (op) { MATHS_OPS[op[0]] = [op[1], op[2]]; });
  var MINUS = '−';
  var NUMBER_TEXT = /^−?(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})*)(?:\.[0-9]+)?$/;      // "3,400", "0.25", "−4"
  var VALUE_TEXT = /^−?(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})*)(?:\.[0-9]+)?[kMBT]?$/;   // ... and "3.4k"
  var MATHS_NAME = /^[A-Za-zΑ-ΡΣ-Ωα-ω]{1,12}$/;
  var ZERO = /^[+\-−]?[$€£]?0+(?:[.,]0+)?%?$/;   // a cell that says none: INK_4
  var FIGURE = /^[+\-−]?[$€£]?[0-9]/;             // a delta line that starts with its figure ("+500")
  var FIGURE_TEXT = /^[+\-−]?[$€£]?[0-9][0-9,.]*[kMBT%]?$/;     // a big number's figure (board.protocol's)
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
    openBigMeta: 'Open big', backToBoard: 'Esc back to the board', today: 'Today'
  };
  var STATE_WORDS = {done: 'Done', next: 'Next', due: 'Due'};   // a timeline node's state, its non-colour cue
  // Each kind's glyph (holo's icons, drawn by tokens.css) and its name (the index strip's accessible names).
  var GLYPHS = {text: 'read-all', calendar: 'calendar', mail_thread: 'mail', mail_list: 'inbox', todos: 'todos',
                picture: 'image', table: 'table', chart: 'chart', timeline: 'timeline', maths: 'maths',
                number: 'chart'};
  var KIND_WORDS = {text: 'Answer', calendar: 'Calendar', mail_thread: 'Mail', mail_list: 'Mail list',
                    todos: 'To-dos', picture: 'Picture', table: 'Table', chart: 'Chart', timeline: 'Timeline',
                    maths: 'Maths', number: 'Number'};

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

  // ---- part 6, Ask's typed frames (DESIGN-SPEC 3.3): each body exactly its keys, every list 1 to its cap, every number
  // finite and in the board's range, never a boolean; words as text and numbers' words made by the app ----------------

  // A table: Mono caps headers, tabular cells (a zero in INK_4), the changed row banded with its change bright, the
  // change's delta line and a source footer. Full width: never paired.
  KINDS.table = function (body, frame) {
    var data = frame.body;
    if (!exactKeys(data, ['columns', 'rows', 'unit', 'changed', 'change', 'source'])
        || !some(data.columns, TABLE.columns) || !some(data.rows, TABLE.rows)
        || !data.columns.every(function (word) { return words(word, TABLE.column); })
        || !data.rows.every(function (row) {
          return Array.isArray(row) && row.length === data.columns.length
            && row.every(function (cell) { return words(cell, TABLE.cell); });
        })
        || !whole(data.changed, -1, data.rows.length - 1) || !words(data.unit, TABLE.unit)
        || !words(data.change, TABLE.change) || !words(data.source, TABLE.source)) {
      throw new TypeError('table');
    }
    var table = make('table', 'tb');
    var heads = make('tr');
    data.columns.forEach(function (word) {
      var cell = make('th', '', word);
      cell.scope = 'col';
      heads.appendChild(cell);
    });
    var top = make('thead');
    top.appendChild(heads);
    var rows = make('tbody');
    data.rows.forEach(function (row, index) {
      var line = make('tr', index === data.changed ? 'hot' : '');
      row.forEach(function (cell) {
        line.appendChild(make('td', ZERO.test(cell) ? 'z' : '', cell));
      });
      rows.appendChild(line);
    });
    table.append(top, rows);
    body.appendChild(table);
    if (data.changed >= 0 && data.change) {
      body.appendChild(deltaLine(data.change));
    }
    if (data.source) {
      body.appendChild(make('p', 'tb-src t-badge', data.source));
    }
  };

  // A chart: bars from zero (labels and values in words, the values in Mono), the changed bar bright with a dashed
  // ghost tick at its old value, and its delta line ("+500 Software · was 2.9k on Friday"). Small: pairs.
  KINDS.chart = function (body, frame) {
    var data = frame.body;
    if (!exactKeys(data, ['bars', 'unit', 'changed', 'was', 'change']) || !some(data.bars, CHART.bars)
        || !data.bars.every(function (bar) {
          return exactKeys(bar, ['label', 'value', 'text']) && words(bar.label, CHART.label) && boardNumber(bar.value)
            && words(bar.text, CHART.text) && VALUE_TEXT.test(bar.text);
        })
        || !whole(data.changed, -1, data.bars.length - 1) || !words(data.unit, TABLE.unit)
        || !words(data.change, TABLE.change)
        || !(data.was === null || (boardNumber(data.was) && data.changed >= 0))) {
      throw new TypeError('chart');
    }
    var values = data.bars.map(function (bar) { return bar.value; });
    if (data.was !== null) {
      values.push(data.was);
    }
    var low = Math.min(0, Math.min.apply(null, values)), high = Math.max(0, Math.max.apply(null, values));
    var span = high - low || 1;
    var at = function (value) { return (value - low) / span; };
    var grid = make('div', 'ch-bars');
    data.bars.forEach(function (bar, index) {
      var track = make('div', 'ch-tr');
      track.setAttribute('aria-hidden', 'true');
      var fill = make('i', 'ch-fl' + (index === data.changed ? ' hot' : '') + (bar.value < 0 ? ' neg' : ''));
      reach(fill, Math.min(at(0), at(bar.value)), Math.abs(at(bar.value) - at(0)));
      track.appendChild(fill);
      if (index === data.changed && data.was !== null) {
        var ghost = make('i', 'ch-was');
        reach(ghost, at(data.was), 0);
        track.appendChild(ghost);
      }
      grid.append(make('span', 'ch-l', bar.label), track, make('span', 'ch-v', bar.text));
    });
    body.appendChild(grid);
    if (data.changed >= 0 && data.change) {
      body.appendChild(deltaLine(data.change));
    }
  };

  // A timeline: nodes with their words - DONE a mint check, NEXT a cyan ring, DUE - and a TODAY marker. In a row of
  // its own up to four nodes from a 300 px frame (opened big, any number); otherwise one node per row, TODAY its own
  // row. Small: pairs.
  KINDS.timeline = function (body, frame) {
    var data = frame.body;
    if (!exactKeys(data, ['nodes', 'today']) || !some(data.nodes, TIMELINE.nodes)
        || !data.nodes.every(function (node) {
          return exactKeys(node, ['when', 'words', 'state']) && words(node.when, TIMELINE.when)
            && words(node.words, TIMELINE.words) && NODE_STATES.indexOf(node.state) >= 0;
        })
        || !whole(data.today, -1, data.nodes.length)) {
      throw new TypeError('timeline');
    }
    var count = data.nodes.length;
    var line = make('div', 'tline' + (count <= 4 ? ' few' : ''));   // (never "tl": a frame's corner)
    line.style.setProperty('--n', String(count));
    var rail = make('i', 'tl-rail');
    rail.setAttribute('aria-hidden', 'true');
    line.appendChild(rail);
    var today = null;
    if (data.today >= 0) {
      line.style.setProperty('--today', todayAt(data.today, count));
      var past = make('i', 'tl-past');
      past.setAttribute('aria-hidden', 'true');
      line.appendChild(past);
      today = make('div', 'tl-today' + (data.today * 2 > count ? ' right' : ''));
      today.appendChild(make('span', 't-badge', WORDS.today));
    }
    data.nodes.forEach(function (node, index) {
      if (today && index === data.today) {
        line.appendChild(today);
      }
      var item = make('div', 'tl-n' + (index === 0 ? ' first' : '') + (index === count - 1 ? ' last' : '')
                       + (node.state ? ' ' + node.state : ''));
      var dot = make('i', 'tl-o');
      if (node.state === 'done') {
        dot.appendChild(glyph('check', 'tl-mark'));
      }
      item.append(dot, make('span', 'tl-when t-badge', node.when), make('span', 'tl-words', node.words),
                  make('span', 'tl-state t-badge', node.state ? STATE_WORDS[node.state] : ''));
      line.appendChild(item);
    });
    if (today && data.today === count) {
      line.appendChild(today);
    }
    body.appendChild(line);
  };

  // Maths: numbered steps - a name, then its line - and the answer boxed in HOLO_HI. Every element of an expression is
  // made here from the checked tree (createElement and textContent only): never markup, never TeX. Under 300 px a step's
  // name goes above its line. Small: pairs.
  KINDS.maths = function (body, frame) {
    var data = frame.body;
    var budget = {left: MATHS.nodes};
    if (!exactKeys(data, ['steps', 'answer']) || !some(data.steps, MATHS.steps)
        || !data.steps.every(function (step) { return mathsStep(step, budget); }) || !mathsStep(data.answer, budget)) {
      throw new TypeError('maths');
    }
    var grid = make('div', 'mx');
    data.steps.forEach(function (step, index) {
      mathsRow(grid, index + 1, step, false);
    });
    mathsRow(grid, data.steps.length + 1, data.answer, true);
    body.appendChild(grid);
  };

  // A big number (the canvas, part 2): the app builds it from a chart's or a table's change - the figure large, what
  // it was and is under it, and where it comes from as the frame's footer. Every word as text.
  KINDS.number = function (body, frame) {
    var data = frame.body;
    if (!exactKeys(data, ['figure', 'text', 'source']) || !words(data.figure, CHART.text) || !FIGURE_TEXT.test(data.figure)
        || !words(data.text, TABLE.change) || !words(data.source, TABLE.source)) {
      throw new TypeError('number');
    }
    body.appendChild(make('p', 'nb-fig', data.figure));
    if (data.text) {
      body.appendChild(make('p', 'nb-text', data.text));
    }
    if (data.source) {
      body.appendChild(make('p', 'nb-src t-badge', data.source));
    }
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
  var mathsLines = new WeakMap();              // a maths line (.mx-e) -> its checked tree, its layout (fitMaths)
  var mathsTimer = 0;                          // the board's maths measured once a change is laid out (scheduleMaths)
  var lit = '';
  var litCue = false;                          // the app draws the cue line's native part to the lit frame
  var stub = null;                             // the cue line's last 15 px: {el, id}
  var big = null;                              // open big: {id, rec, card, state: wait / open / closing / shut, box, turn}
  var bigTurn = 0;                             // bumped by each open asked, each step back and each "laid" made ready:
                                               // a "laid" is sent only while its own step is the latest (a close's
                                               // only when no open was asked since its "shut": big.turn)
  var bigLayer = null;                         // the big frame's layer over the whole view
  var focusOwed = false;                       // big ended with the keyboard and nothing on the board could take it:
                                               // the next frame shown does (refresh), never <body>
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

  // ---- part 6's helpers --------------------------------------------------------------------------------------------

  function exactKeys(value, keys) {            // an object with exactly ``keys``, each present
    return onlyKeys(value, keys) && keys.every(function (key) { return key in value; });
  }

  function some(value, cap) {                  // a list of 1 to ``cap`` items
    return list(value, cap) && value.length > 0;
  }

  function boardNumber(value) {                // a finite number in the board's range (never a boolean, never words)
    return typeof value === 'number' && isFinite(value) && Math.abs(value) < NUMBER_LIMIT;
  }

  function reach(node, from, width) {          // a chart's fill or ghost tick, in fractions of its track
    node.style.setProperty('--x0', (Math.max(0, Math.min(1, from)) * 100).toFixed(3) + '%');
    node.style.setProperty('--xw', (Math.max(0, Math.min(1, width)) * 100).toFixed(3) + '%');
  }

  function dotAt(index, count) {               // a timeline node's dot in a row: the first and last at the ends
    if (index <= 0) {
      return '9px';
    }
    if (index >= count - 1) {
      return 'calc(100% - 9px)';
    }
    return ((index + 0.5) / count * 100).toFixed(3) + '%';
  }

  function todayAt(today, count) {             // TODAY before node ``today``: half way from the node before it
    if (today <= 0) {
      return '0px';
    }
    if (today >= count) {
      return '100%';
    }
    return 'calc((' + dotAt(today - 1, count) + ' + ' + dotAt(today, count) + ') / 2)';
  }

  function deltaLine(text) {                   // a changed figure's words: the delta glyph, its figure first and bright
    var line = make('p', 'delta');
    line.appendChild(glyph('delta', 'delta-mark', true));
    var space = text.indexOf(' ');
    var lead = space > 0 ? text.slice(0, space) : text;
    if (FIGURE.test(lead)) {
      line.append(make('b', 'delta-lead', lead), document.createTextNode(space > 0 ? text.slice(space) : ''));
    } else {
      line.appendChild(document.createTextNode(text));
    }
    return line;
  }

  function mathsTree(node, depth, budget) {    // a checked expression (as board.protocol checks it)
    if (depth > MATHS.depth || (budget.left -= 1) < 0 || !Array.isArray(node) || !node.length
        || typeof node[0] !== 'string') {
      return false;
    }
    var head = node[0];
    if (head === 'num') {
      return node.length === 2 && words(node[1], MATHS.text) && NUMBER_TEXT.test(node[1]);
    }
    if (head === 'name') {
      return node.length === 2 && typeof node[1] === 'string' && MATHS_NAME.test(node[1]);
    }
    var arity = MATHS_OPS[head];
    if (!arity || node.length - 1 < arity[0] || node.length - 1 > arity[1]) {
      return false;
    }
    for (var index = 1; index < node.length; index += 1) {
      if (!mathsTree(node[index], depth + 1, budget)) {
        return false;
      }
    }
    return true;
  }

  function mathsStep(step, budget) {
    return exactKeys(step, ['name', 'expr']) && words(step.name, MATHS.name) && mathsTree(step.expr, 1, budget);
  }

  function mathsRow(grid, number, step, answer) {
    var line = make('div', 'mx-e');
    mathsLines.set(line, {expr: step.expr, answer: answer, laid: laysOut(step.expr), flat: false, width: 0});
    fillLine(line, false);
    grid.append(make('span', 'mx-i', String(number)), make('span', 'mx-k t-badge', step.name), line);
  }

  function laysOut(node) {                     // an expression with a fraction or a root in it (laid out, not one line)
    return node[0] === 'frac' || node[0] === 'root' || node.slice(1).some(function (arg) {
      return Array.isArray(arg) && laysOut(arg);
    });
  }

  function fillLine(line, flat) {              // a maths line from its tree: its fractions and roots laid out or inline
    var entry = mathsLines.get(line);
    var expression = mathsNode(entry.expr, flat);
    if (entry.answer) {
      var box = make('span', 'mx-ans');
      box.appendChild(expression);
      line.replaceChildren(box);
    } else {
      line.replaceChildren(expression);
    }
    entry.flat = flat;
  }

  function spills(line) {                      // a fraction or a root of the line that does not stay inside it, or that
    var edge = line.getBoundingClientRect();   // its own words overflow - or a root whose radicand wraps
    return Array.prototype.some.call(line.querySelectorAll('.mx-frac, .mx-root'), function (node) {
      var box = node.getBoundingClientRect();
      return box.left < edge.left - 0.5 || box.right > edge.right + 0.5 || node.scrollWidth > node.clientWidth + 1;
    }) || Array.prototype.some.call(line.querySelectorAll('.mx-rad'), wraps);
  }

  function wraps(radicand) {                   // its own words on more than one line (its sign would stand by the last
    var places = [];                           // one alone); a fraction's, a power's or a root's inside it keep theirs
    Array.prototype.forEach.call(radicand.querySelectorAll('.mx-n, .mx-v, .mx-op, .mx-b'), function (leaf) {
      var layout = leaf.parentElement.closest('.mx-frac, .mx-sup, .mx-root, .mx-idx');
      if (!layout || !radicand.contains(layout)) {
        places.push(leaf.getBoundingClientRect());
      }
    });
    return places.some(function (one) {
      return places.some(function (other) { return other.top >= one.bottom - 0.5; });
    });
  }

  // Measured after layout, never from data: a fraction or a root still wider than its line (a fraction shrinks and
  // wraps its parts first, board.css; a root whose radicand would wrap is too wide) reads inline there (a/b, √(…)); the
  // line is laid out again once it is wider than when it fell back (``fresh``: every line is - fitBig's new sizes).
  function fitMaths(scope, fresh) {
    Array.prototype.forEach.call(scope.querySelectorAll('.mx-e'), function (line) {
      var entry = mathsLines.get(line);
      var width = entry && entry.laid ? line.clientWidth : 0;
      if (!width) {
        return;                                // nothing laid out in it, or not shown (a satellite, a parked answer)
      }
      if (entry.flat && (fresh || width > entry.width)) {
        fillLine(line, false);
      }
      if (!entry.flat && spills(line)) {
        fillLine(line, true);
        entry.width = width;
      }
    });
  }

  function scheduleMaths() {                   // the board's maths, measured once this change is laid out (a task of
    if (!mathsTimer) {                         // its own; the big frame's: fitBig, at its fill and at each resize)
      mathsTimer = setTimeout(function () {
        mathsTimer = 0;
        if (root) {
          fitMaths(root);
        }
      }, 0);
    }
  }

  // How an expression reads (board.ask_frames.maths_text says the same in plain words): what binds tighter, where
  // brackets go, and when two factors stand side by side with no sign ("4x", "(x − 1)(x − 3)", "2√3").
  var PREC = {or: 0, '=': 1, '+': 2, '*': 3, '/': 3, pow: 5};

  function negative(node) {                    // a negation, a negative number, or a product that starts with one
    if (node[0] === '-') {
      return node.length === 2;
    }
    if (node[0] === 'num') {
      return node[1].charAt(0) === MINUS;
    }
    return node[0] === '*' && negative(node[1]);
  }

  function positive(node) {                    // a negative node without its sign
    if (node[0] === 'num') {
      return ['num', node[1].slice(1)];
    }
    if (node[0] === '*') {
      return ['*', positive(node[1])].concat(node.slice(2));
    }
    return node[1];
  }

  function prec(node) {
    if (node[0] === 'num') {
      return negative(node) ? 4 : 6;
    }
    if (node[0] === '-') {
      return node.length === 2 ? 4 : 2;
    }
    return node[0] in PREC ? PREC[node[0]] : 6;
  }

  function atom(node) {
    return node[0] === 'name' || node[0] === '()' || (node[0] === 'num' && !negative(node));
  }

  function factorBracketed(node, index) {
    return prec(node) <= 2 || (index > 0 && negative(node));
  }

  function negationBracketed(node) {
    return prec(node) <= 2 || negative(node);
  }

  function starts(node, bracketed) {           // what a factor shows first
    if (bracketed || node[0] === '()') {
      return 'open';
    }
    switch (node[0]) {
      case 'num': return negative(node) ? 'minus' : 'digit';
      case 'name': return node[1].length === 1 ? 'letter' : 'word';
      case 'root': return node.length === 3 ? 'index' : 'root';
      case 'pow': return starts(node[1], !atom(node[1]));
      case '*': return starts(node[1], factorBracketed(node[1], 0));
      case '-': return 'minus';
      default: return node[0];
    }
  }

  function ends(node, bracketed) {             // what a factor shows last
    if (bracketed || node[0] === '()') {
      return 'close';
    }
    switch (node[0]) {
      case 'num': return 'digit';
      case 'name': return node[1].length === 1 ? 'letter' : 'word';
      case 'pow': return 'sup';
      case '*': return ends(node[node.length - 1], factorBracketed(node[node.length - 1], node.length - 2));
      case '-': return node.length === 2 ? ends(node[1], negationBracketed(node[1])) : '-';
      default: return node[0];
    }
  }

  function joined(left, right) {
    return ['letter', 'open', 'root'].indexOf(right) >= 0 && ['digit', 'letter', 'close', 'sup'].indexOf(left) >= 0;
  }

  function mathsSign(text) {
    return make('span', 'mx-op', text);
  }

  function mathsGroup(node, bracketed, flat) { // a node, in brackets when it must be
    var built = mathsNode(node, flat);
    if (!bracketed) {
      return built;
    }
    var group = make('span', 'mx-g');
    group.append(make('span', 'mx-b', '('), built, make('span', 'mx-b', ')'));
    return group;
  }

  // An expression's elements, from the checked tree only. ``flat``: its fractions and roots inline, as
  // board.ask_frames.maths_text writes them (a/b, √(…), the index raised before the sign) - a line too narrow for them.
  function mathsNode(node, flat) {
    var head = node[0], args = node.slice(1), box;
    if (head === 'num') {
      return make('span', 'mx-n', node[1]);
    }
    if (head === 'name') {
      return make('span', 'mx-v', node[1]);
    }
    if (head === '()') {
      return mathsGroup(args[0], true, flat);
    }
    box = make('span', 'mx-x');
    if (head === 'or' || head === '=') {
      args.forEach(function (arg, index) {
        if (index) {
          box.appendChild(mathsSign(head === 'or' ? ' or ' : ' = '));
        }
        box.appendChild(mathsGroup(arg, prec(arg) <= (head === 'or' ? 0 : 1), flat));
      });
    } else if (head === '+') {
      box.appendChild(mathsGroup(args[0], prec(args[0]) <= 1, flat));
      args.slice(1).forEach(function (arg) {
        if (negative(arg)) {
          var shown = positive(arg);
          box.append(mathsSign(' ' + MINUS + ' '), mathsGroup(shown, arg[0] === '-' && negationBracketed(shown), flat));
        } else {
          box.append(mathsSign(' + '), mathsGroup(arg, prec(arg) <= 1, flat));
        }
      });
    } else if (head === '-' && args.length === 1) {
      box.append(mathsSign(MINUS), mathsGroup(args[0], negationBracketed(args[0]), flat));
    } else if (head === '-') {
      box.append(mathsGroup(args[0], prec(args[0]) <= 1, flat), mathsSign(' ' + MINUS + ' '),
                 mathsGroup(args[1], negationBracketed(args[1]), flat));
    } else if (head === '*') {
      args.forEach(function (arg, index) {
        var bracketed = factorBracketed(arg, index);
        if (index) {
          var before = args[index - 1];
          if (!joined(ends(before, factorBracketed(before, index - 1)), starts(arg, bracketed))) {
            box.appendChild(mathsSign(' × '));
          }
        }
        box.appendChild(mathsGroup(arg, bracketed, flat));
      });
    } else if (head === '/') {                 // a fraction that divides, inline: "6 ÷ (1/2)" (stacked: no brackets)
      box.append(mathsGroup(args[0], prec(args[0]) <= 2, flat), mathsSign(' ÷ '),
                 mathsGroup(args[1], prec(args[1]) <= 3 || negative(args[1]) || (flat && args[1][0] === 'frac'), flat));
    } else if (head === 'frac' && flat) {
      box.append(mathsGroup(args[0], !atom(args[0]), true), mathsSign('/'), mathsGroup(args[1], !atom(args[1]), true));
    } else if (head === 'frac') {
      box = make('span', 'mx-frac');
      var over = make('span', 'mx-top'), under = make('span', 'mx-bot');
      over.appendChild(mathsNode(args[0], false));
      under.appendChild(mathsNode(args[1], false));
      box.append(over, under);
    } else if (head === 'pow') {
      var power = make('span', 'mx-sup');
      power.appendChild(mathsNode(args[1], flat));
      box.append(mathsGroup(args[0], !atom(args[0]), flat), power);
    } else if (flat) {                         // root, inline: its index raised, the sign, the radicand in brackets
      if (args.length === 2) {
        var raised = make('span', 'mx-sup');
        raised.appendChild(mathsNode(args[1], true));
        box.appendChild(raised);
      }
      box.append(make('span', 'mx-sign', '√'), mathsGroup(args[0], !atom(args[0]), true));
    } else {                                   // root: its index (when it has one), the sign, the radicand under its bar
      box = make('span', 'mx-root');
      if (args.length === 2) {
        var index = make('span', 'mx-idx');
        index.appendChild(mathsNode(args[1], false));
        box.appendChild(index);
      }
      var radicand = make('span', 'mx-rad');
      radicand.appendChild(mathsNode(args[0], false));
      box.append(make('span', 'mx-sign', '√'), radicand);
    }
    return box;
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
               ixGlyph: null, ixKind: '', fold: false, fx: false, rev: 0, nat: null, booting: false, wantBoot: false,
               folding: false};
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
    rec.rev += 1;                              // (the canvas measures it again)
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

  function boardFrames() {                     // the frames shown, in board order (every group's, top to bottom); never
    return Array.prototype.filter.call(root.querySelectorAll('.frame'), function (node) {   // one whose group has
      var rec = records.get(node);                                                         // left (its frames go next)
      return current(rec) && !rec.closed && !rec.away && !parked(rec) && !(rec.group && !groups.has(rec.group));
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
      rec.sat = canvas ? (rec.fold && !rec.closed)                          // the canvas: by room (packCanvas)
        : (!!group && group.older && !rec.pinned && !rec.expanded && !rec.closed);
      rec.el.classList.toggle('away', rec.away && !rec.folding);
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
    if (focusOwed) {                           // the keyboard owed to the board since big ended with nothing on it
      var owed = document.activeElement;
      if (owed && owed !== document.body) {
        focusOwed = false;                     // it went somewhere meanwhile
      } else if (document.hasFocus()) {
        focusOwed = !focusBoard();
      }
    }
    scheduleView();
    scheduleLayout();
    scheduleMaths();
    schedulePack('');                          // the canvas: what changed is placed (never moves what stays)
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
    if (canvas) {                              // the orb's own moves: calm slower, off still
      orbMotionChanged();
    }
  }

  function landed(rec, step) {                 // the board's sounds (part 5c): the app hears a frame land, then settle
    if (current(rec) && onDesk()) {
      post('landed', {id: rec.id, step: step});
    }
  }

  function arrive(rec) {                       // the outline traces, the ticks snap on, the content comes in
    if (canvas) {
      rec.wantBoot = true;                     // the canvas: it boots once it has its place (packCanvas)
      schedulePack('full');
      return;
    }
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
    if (canvas && K > 0 && el.animate) {       // the canvas: its cell keeps its size - the fade alone
      after(el.animate([{opacity: 0.25}, {opacity: 1}], {duration: FADE_MS * K, easing: easeOut}), function () {
        if (done) {
          done();
        }
      });
      return;
    }
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
      // Bring back is offered CLOSED_MS; the app takes the frame away then (it is the source of truth). The keyboard on
      // its Bring back moves to the frame itself first (the same frame: no focus or reading report, no scroll): hidden,
      // the button would drop it to <body>, and the app's remove (which hands a leaving frame's keyboard to a staying
      // one) comes a few ms later.
      rec.closedTimer = setTimeout(function () {
        if (el.contains(document.activeElement)) {
          el.focus({preventScroll: true});
        }
        el.classList.add('expired');
      }, CLOSED_MS);
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
    if (canvas) {                              // the canvas: it fades in its cell (the others stay where they are)
      el.classList.add('folding');
      after(el.animate([{opacity: 1, transform: 'none'}, {opacity: 0, transform: 'scale(0.97)'}],
                       {duration: FADE_MS * K, easing: easeOut, fill: 'forwards'}), function () {
        el.remove();
        refresh();
      });
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
    if (!canvas) {                             // (the canvas never scrolls)
      node.scrollIntoView({block: 'nearest', behavior: K === 0 ? 'auto' : 'smooth'});
    }
  }

  function move(rec, key) {
    if (canvas) {
      goTo(nextOnCanvas(rec, key));            // the canvas: by where the frames are on the screen
      return;
    }
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
  var BIG_KEYS = ['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', ' ', 'Home', 'End'];   // open big's body scrolls

  function onKey(event) {
    if (event.ctrlKey || event.altKey || event.metaKey) {
      return;
    }
    document.documentElement.classList.add('keys');   // the keyboard's own focus shows (as :focus-visible)
    if (!inCanvasChrome(event.target)) {
      signOfReading();                         // (a key on one of the canvas's corner buttons is not the board's)
    }
    var key = event.key;
    if (big && (big.state === 'wait' || big.state === 'open')) {
      if (key === 'Escape') {
        shrink(true);                          // Esc leaves open big first (the board again; the next Esc: the ask bar)
        event.preventDefault();
      } else if (key === 'Tab') {
        cardTab(event.shiftKey);               // the focus stays in the big frame (the board under it is inert)
        event.preventDefault();
      } else if (BIG_KEYS.indexOf(key) >= 0
                 && !(key === ' ' && event.target.closest && event.target.closest('button'))) {
        scrollBig(key, event.shiftKey);        // its body scrolls (a control of it keeps its own Space)
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
      if (canvas) {
        return;                                // the canvas is the whole window: the board's own box says so
      }
      reading.pointer = true;
      sendReading(true);
    });
    html.addEventListener('mouseleave', function () {
      reading.pointer = false;
      sendReading(true);
    });
    document.addEventListener('focusin', function (event) {
      reading.focus = !inCanvasChrome(event.target);
      sendReading(false);
    });
    document.addEventListener('focusout', function () {
      setTimeout(function () {
        reading.focus = pageFocused() && !inCanvasChrome(document.activeElement);
        sendReading(false);
      }, 0);
    });
    window.addEventListener('blur', function () {
      reading.focus = false;
      sendReading(false);
    });
    document.addEventListener('wheel', function (event) {
      if (!canvas || overBoard(event)) {
        signOfReading();
      }
    }, {passive: true, capture: true});
    document.addEventListener('pointerdown', function (event) {
      html.classList.remove('keys');           // a press: no keyboard focus ring until the next key
      if (!canvas || overBoard(event)) {
        signOfReading();
      }
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
    schedulePack('full');
    syncCanvasBoard();
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
    var pill = stripParts.pill, shown = any && onDesk();
    if (shown && !pill.classList.contains('shown') && K > 0) {
      pill.classList.add('arriving');          // its fade in, dropped once over (entranceOver)
    }
    pill.classList.toggle('shown', shown);
    if (!shown) {
      pill.classList.remove('arriving');
    }
  }

  function entranceOver(event) {               // the desk's or the pill's entrance is over: it is dropped, since an
    if (event.animationName === 'desk-in') {   // animation left on the board, the bar or the pill - even one over -
      document.documentElement.classList.remove('desk-in');   // outranks open big's dim on their opacity: the dim
    } else if (event.animationName === 'show' && stripParts && event.target === stripParts.pill) {   // jumped there,
      stripParts.pill.classList.remove('arriving');            // or eased a frame behind the frame's growth
    }
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
      if (canvas) {
        rec.el.classList.add('unplaced');      // the canvas: out of sight until it has its place
      }
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
      dropHead(group);
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
      schedulePack('full');                    // the canvas: the newest answer's frames take the best places
    }
    syncHead(group);
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
    dropHead(group);
    var before = document.activeElement;       // where the keyboard was (given back below only if this took it)
    var hadFocus = group.el.contains(before);
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
    // The keyboard never stays with the group leaving, nor on <body> where this left it (its big frame's end dropped
    // it there); a <body> focus the owner made (a click beside the frames) stays as it is - nothing moves, nothing
    // scrolls. Never while the board is inert: big's own end gives the keyboard back (focusBack). Nothing on the board
    // just now: the next frame shown takes it.
    var active = document.activeElement;
    var dropped = (!active || active === document.body) && !!before && before !== document.body;
    if (!root.inert && (hadFocus || el.contains(active) || (dropped && document.hasFocus()))) {
      focusOwed = !focusBoard();
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
    barFrames = message.frames;
    syncCanvasBoard();
    if (barPinned === 0 && filter === 'pinned') {
      filter = 'all';                          // nothing pinned any more: the whole board again
      schedulePack('full');
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
    deskOn = message.state === 'enter';
    syncCanvasBoard();                           // the canvas: the board's box shows, or is away (folded, empty)
    if (message.state === 'enter') {
      schedulePack('full');
    }
    if (message.state === 'enter' && K > 0 && !canvas) {    // (motion off: there at once, no entrance to drop; the
                                                            // canvas: each frame boots in its own place)
      void html.getBoundingClientRect();         // the fade and slide start again (dropped once over: entranceOver)
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
    var taken = [], back = [];
    frames.forEach(function (rec) {
      if (gone.has(rec.id) && rec.el.contains(document.activeElement)) {
        hadFocus = rec;
      }
      if (gone.has(rec.id) !== rec.cleared && !rec.closed && !parked(rec)) {
        (rec.cleared ? back : taken).push(rec);
      }
      rec.cleared = gone.has(rec.id);
    });
    drawIntoOrb(taken);                        // the canvas: each folds to a line the orb draws in ...
    refresh();
    drawOutOfOrb(back);                        // ... and Bring back lets them out again
    schedulePack('full');
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
    if (!stripParts || canvas) {               // (the canvas draws no strip)
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
    var inControls = inCanvasChrome(document.activeElement);
    var part = message.part;
    if (canvas) {                              // the canvas: the board, its strip, the corner buttons, the ask line
      if (part === 'next') {
        part = inControls ? 'ask' : (inStrip ? 'controls' : 'strip');
      } else if (part === 'prev') {
        part = inControls ? 'strip' : (inStrip ? 'board' : 'ask');
      }
      if (part === 'strip' && !focusStrip()) {
        part = message.part === 'prev' ? 'board' : 'controls';
      }
      if (part === 'board' && !focusBoard()) {
        part = message.part === 'prev' ? 'ask' : 'controls';
      }
      if (part === 'controls' && !focusControls()) {
        part = 'ask';
      }
    } else if (part === 'controls') {
      part = 'ask';                            // no canvas: nothing of it to take the keyboard
    } else if (part === 'next') {
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
      if (!canvas) {
        measureWidth(box.w);                   // the board's own width, as it was before the view went wide (a resize
      }                                        // that came first measured the wide view)
      placeBig();
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
    if (canvas) {
      return !!big && big.state !== 'shut';    // the canvas: the view is the whole window from the start
    }
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

  function bigHasFocus() {                     // the keyboard is the big frame's: in its layer, or on nothing while the
    var active = document.activeElement;       // page has it (a click beside it took it there)
    return (!!bigLayer && !!active && bigLayer.contains(active))
      || (document.hasFocus() && (!active || active === document.body));
  }

  function focusBack(rec) {                    // big is over: the keyboard goes back to its frame while the board still
    var node = current(rec) && !rec.away && !parked(rec) ? target(rec) : null;     // has it, else to the board (its
    if (node) {                                                                      // frame in view): never <body>
      node.focus({preventScroll: true});
    }
    if (!node || document.activeElement !== node) {
      focusOwed = !focusBoard();               // (nothing on the board just now: the next frame shown takes it)
    }
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
    if (wide && !canvas) {
      body.style.setProperty('--bx', big.box.x + 'px');
      body.style.setProperty('--by', big.box.y + 'px');
      body.style.setProperty('--bw', big.box.w + 'px');
      body.style.setProperty('--bh', big.box.h + 'px');
    }
    body.classList.toggle('wide', wide && !canvas);   // (the canvas: the board's box is its own already)
    if (big && big.card && big.card.isConnected) {
      fitBig(big.card, big.rec);               // the card is the view's size: laid out for it again
    }
    if (big && big.state === 'wait' && wide) {
      big.state = 'open';
      grow();
    } else if (big && big.state === 'shut' && !wide) {
      var asked = big.turn !== bigTurn;        // an open asked since its "shut": that open's own "laid" answers the app
      big = null;                              // the app set the geometry back: the board is its own again, measured
      measureWidth();                          // at its real width (it re-flows only when that changed meanwhile)
      if (!asked) {
        laidOut();                             // (this close's "laid" only ever answers this close)
      }
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
    body.addEventListener('scroll', function () { cutCue(body); }, {passive: true});
    if (inside) {
      card.focus({preventScroll: true});       // a control of it had the focus: it stays in the big frame
    }
    if (card.isConnected) {
      fitBig(card, rec);                       // (a card that grows yet is fitted once it is in the view: grow)
    }
  }

  // Open big (DESIGN-SPEC 3.5) lays a typed frame out to fit its card: a table's or maths' big sizes step down
  // (board.css fit-1 to fit-3, the board's own 448 px sizes, never smaller) until its body holds all of it; past that
  // the body scrolls, with its cue (cutCue) and its keys (scrollBig).
  function fitBig(card, rec) {
    var body = card.querySelector(':scope > .f-body');
    if (!body) {
      return;
    }
    var steps = FIT_KINDS.indexOf(rec.data.kind) >= 0 ? FIT_STEPS : 0;
    for (var level = 0; level <= steps; level += 1) {
      for (var step = 1; step <= FIT_STEPS; step += 1) {
        card.classList.toggle('fit-' + step, step === level);
      }
      fitMaths(card, true);
      if (body.scrollHeight <= body.clientHeight) {
        break;
      }
    }
    cutCue(body);
  }

  function cutCue(body) {                      // the cut edge fades while more of the body is out of sight that way
    var more = body.scrollHeight - body.clientHeight;
    body.classList.toggle('cut-start', more > 0 && body.scrollTop > 1);
    body.classList.toggle('cut-end', more > 0 && body.scrollTop < more - 1);
  }

  function scrollBig(key, back) {              // open big's keys (3.6): its body a line, a page or to an end
    var body = big && big.state === 'open' && big.card ? big.card.querySelector(':scope > .f-body') : null;
    if (!body) {
      return;
    }
    var page = Math.max(LINE_PX, body.clientHeight - LINE_PX);
    var by = {ArrowDown: LINE_PX, ArrowUp: -LINE_PX, PageDown: page, PageUp: -page}[key];
    var top = key === 'Home' ? 0 : key === 'End' ? body.scrollHeight
      : body.scrollTop + (by === undefined ? (back ? -page : page) : by);   // Space: a page down, Shift+Space up
    body.scrollTo({top: top, behavior: K === 0 ? 'auto' : 'smooth'});
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
    card.tabIndex = 0;                         // a stop of its own: a Tab into the view lands on it (never on <body>)
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
    // Held until it grows (part 5c fixes, round 2): there, unseen and untouched, nothing dimmed yet - every frame the
    // app's view can show before its growth starts is the board as it was, never the big frame at its full size. On the
    // layer: fillCard rewrites the card's own class.
    bigLayer.classList.add('held');
    bigLayer.appendChild(card);
    document.body.appendChild(bigLayer);
    fitBig(card, rec);                         // at the view's size (held: laid out, not yet seen)
    underBig(true);
    card.focus({preventScroll: true});         // the focus goes with it (the frame under it is inert now)
    var from = rec.el.getBoundingClientRect();
    var start = function () {
      if (big !== mine || mine.state !== 'open' || mine.card !== card) {
        return;                                // gone back (or another frame big) before its picture was ready
      }
      bigLayer.classList.remove('held');       // it grows from here, and the backdrop and the other frames go to 20 %
      document.body.classList.add('dim');      // with it
      laidOut();
      card.focus({preventScroll: true});
      if (K === 0 || !card.animate || from.width < 1 || from.height < 1) {
        return;                                // motion off: there at once
      }
      var room = card.getBoundingClientRect();
      var width = room.width || window.innerWidth, height = room.height || window.innerHeight;
      card.animate([
        {transform: 'translate(' + (from.left - room.left) + 'px, ' + (from.top - room.top) + 'px) scale('
          + (from.width / width) + ', ' + (from.height / height) + ')', opacity: 0.35},
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
    var hadFocus = bigHasFocus();
    var held = !!bigLayer && bigLayer.classList.contains('held');
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
      if (hadFocus) {
        focusBack(rec);
      }
      post('big', {step: 'shut'});
      mine.turn = bigTurn;                     // an open asked after this: the board's "laid" is that open's (placeBig)
      if (!isWide()) {
        placeBig();                            // the view was never wide (or is narrow again already)
      }
    };
    if (!card || K === 0 || !card.animate || !current(rec) || rec.away || rec.closed || held) {
      done();                                  // (held: it never grew, so it goes at once)
      return;
    }
    var to = rec.el.getBoundingClientRect();
    var room = card.getBoundingClientRect();
    var width = room.width || window.innerWidth, height = room.height || window.innerHeight;
    after(card.animate([
      {transform: 'none', opacity: 1},
      {opacity: 1, offset: 0.6},
      {transform: 'translate(' + (to.left - room.left) + 'px, ' + (to.top - room.top) + 'px) scale('
        + (to.width / width) + ', ' + (to.height / height) + ')', opacity: 0}
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

  // ---- the canvas (PLAN-canvas part 1): Home is this page - the orb in three forms and the corners' controls --------
  // The app sends two kinds: "orb" (a state word, a form, two modifiers, quiet hours' dim and two levels) and "canvas"
  // (the corners' words, a count and which of this page's own buttons show). Every element is made here, once, at the
  // first of them; every label is this page's own (CANVAS_WORDS), and a press posts "go" with a word of the list
  // Python accepts (board.protocol.GO_WORDS) - or the board's own "clear" for Clear view. The orb is one 2D canvas
  // drawn here each frame (below: why, and how); its moves between forms are springs and a curve - the turn a
  // little past and back, the rings clicking shut, the curved trip with its trail and bounce. Calm: x1.6 and no
  // overshoot; off: cuts and a short fade, and nothing turns.

  var ORB_STATES = ['idle', 'listening', 'thinking', 'speaking', 'needs', 'waiting'];
  var ORB_FORMS = ['rings', 'badge', 'corner'];
  var CANVAS_TONES = ['holo', 'amber', 'steel'];
  var CANVAS_BUTTONS = ['history', 'board', 'briefing', 'approvals', 'followups', 'meetings', 'agents', 'live',
                        'memory', 'settings', 'desktop', 'minimize', 'maximize', 'close', 'mute', 'day'];
  var CANVAS_CAPS = {clock: 40, word: 40, hold: 24, rec: 40, wake: 24, approvals: 999, level: 100};
  var CANVAS_WORDS = {
    name: 'JARVIS', wordmark: 'J.A.R.V.I.S.', history: 'History', clear: 'Clear view', board: 'Show board',
    briefing: 'Briefing', approvals: 'Approvals', followups: 'Follow-ups', meetings: 'Meetings', agents: 'Agents',
    live: 'Live', memory: 'Memory', settings: 'Settings', desktop: 'Desktop', mute: 'Mute', unmute: 'Unmute',
    undo: 'Undo', stop: 'Stop', level: 'Lvl ', db: ' dB', micIdle: 'Mic idle',
    orb: 'Jarvis', minimize: 'Minimise the window', maximize: 'Maximise or restore the window', close: 'Close',
    clearAll: 'Clear view: take away every unpinned frame', boardBack: 'Show the board again',
    desktopSwitch: 'Jarvis on your desktop', day: 'Today and deadlines', briefingNew: 'Briefing, ready',
    approvalsWait: ' waiting'
  };
  // The screens' buttons, left to right (the mock's order; the others take their place only while they can work).
  var SCREEN_BUTTONS = ['briefing', 'approvals', 'followups', 'meetings', 'agents', 'live', 'memory', 'settings'];
  var canvas = null;                           // the canvas's elements and state, made at the first orb / canvas message
  var deskOn = false;                          // the board is on the canvas (desk: enter) or away (leave, folded)
  var barFrames = 0;                           // the bar's count of frames (Clear view shows while there are some)

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function canvasButton(word, label, cls, press) {
    var node = button('c-btn' + (cls ? ' ' + cls : ''), '', press || function () { post('go', {to: word}); });
    if (label) {
      node.textContent = label;
    }
    node.hidden = true;                        // shown only while the app says pressing it works
    return node;
  }

  function iconButton(word, icon, label) {
    var node = canvasButton(word, '', 'icon quiet');
    node.setAttribute('aria-label', label);
    node.appendChild(glyph(icon, ''));
    return node;
  }

  // ---- the orb: ONE 2D canvas, drawn by this page (PLAN-canvas part 1) ---------------------------------------------
  // The web engine runs without a GPU (Jarvis's own flags), so a stack of glowing layers turned in 3D is composited
  // on the processor - measured at about 9 frames a second at 1920 x 1080. The orb is therefore drawn here, each
  // frame, as what it is: rings in space on one lit axis. Each ring is a circle in its own plane; the pose (a 3 x 3
  // rotation) gives the plane, the ring's depth along the axis its place and its size (the mock's 1200 px
  // perspective), and the canvas's transform turns the circle into the ellipse the owner sees - so arcs, ticks and
  // dashes keep their places as the ring spins, and a stroke thins where it is seen edge on. A glow is the stroke
  // drawn again, wider and fainter, under itself. Everything is numbers: the model below (ORB), eased each frame.

  var POSE_Z = -15, POSE_Y = 57;               // the rings' pose (the mock's rotateZ(-15deg) rotateY(57deg))
  var PERSPECTIVE = 1200;                      // px, as the mock's stage
  var STAGE = 640, REACH_X = 720, REACH_Y = 620;   // the mock's stage; the canvas's own box around the orb's centre
  var CORNER_SCALE = 0.34, CORNER_INSET = 112, TRAIL = 3, METER_BARS = 8;
  var TRIP_MS = 900, INTRO_MS = 900, CUT_FADE_MS = 140, CLICK_MS = 170;
  // The rings, back to front along the axis: its radius (the mock's ring, to the middle of its line), its depth,
  // its line, how far its glow reaches and how strong it is, its colour key, its arcs (degrees, 0 at 3 o'clock,
  // clockwise) and its own turn (degrees a second). ``halo`` (the two whole rings): the glow as [px from the line,
  // strength] stops - one smooth fill in the ring's own plane, where the mock has a blurred shadow.
  var RINGS = [
    {name: 'far', r: 214, z: -190, w: 1.5, glow: 0, power: 0, key: 'hi', alpha: 0.55, dash: [4, 4], turn: 0},
    {name: 'need', r: 214, z: -190, w: 3.5, glow: 16, power: 0.9, key: 'amber', arcs: [[-150, -30]], turn: 138},
    {name: 'ticks', r: 178, z: -150, w: 2.4, glow: 0, power: 0, key: 'tick', ticks: 72, turn: 9},
    {name: 'arc', r: 163.5, z: -60, w: 3, glow: 14, power: 0.5, key: 'c', arcs: [[-135, -45]], turn: 51.4},
    {name: 'main', r: 131, z: 0, w: 8, glow: 34, power: 0.85, key: 'c', turn: 0,
     halo: [[-34, 0], [-26, 0.05], [-18, 0.16], [-10, 0.36], [-4, 0.62], [4, 0.74], [10, 0.52], [19, 0.28],
            [29, 0.13], [39, 0.05], [50, 0]]},
    {name: 'inner', r: 97, z: 0, w: 2, glow: 16, power: 0.8, key: 'white', turn: 0, haloKey: 'hi',
     halo: [[-15, 0], [-9, 0.12], [-4, 0.34], [-1, 0.6], [1, 0.72], [5, 0.42], [11, 0.16], [19, 0]]},
    {name: 'front', r: 111.5, z: 130, w: 3, glow: 14, power: 0.75, key: 'white', haloKey: 'hi', arcs: [[135, 405]],
     turn: 32.7},
    {name: 'front2', r: 149.2, z: 190, w: 1.5, glow: 10, power: 0.7, key: 'hi', arcs: [[-135, -45], [45, 135]],
     turn: -60}
  ];
  // How fast each ring turns in each state, as a rate of its own turn; thinking: against each other, fast.
  var RATES = {
    idle: {ticks: 1, arc: 1, front: 1, front2: 1}, listening: {ticks: 0.5, arc: 0.55, front: 0.6, front2: 0.6},
    thinking: {ticks: -11, arc: 3.4, front: -4.5, front2: 3}, speaking: {ticks: 1.4, arc: 1.5, front: 1.3, front2: 1.4},
    needs: {ticks: 0.8, arc: 0.8, front: 0.8, front2: 0.8}, waiting: {ticks: 0.45, arc: 0.4, front: 0.4, front2: 0.4}
  };
  var LEANS = {listening: [7, -25], thinking: [-5, 7]};      // the lean (degrees about x, about y) a state adds
  var INKS = {                                 // Jarvis's cyan as the mock draws it; the offline voice's steel
    live: {c: [53, 211, 240], hi: [143, 235, 255], tick: [191, 244, 255], white: [242, 253, 255], amber: [245, 177, 74]},
    steel: {c: [143, 169, 179], hi: [180, 198, 204], tick: [195, 210, 214], white: [226, 234, 237], amber: [245, 177, 74]}
  };
  var ORB = null;                              // the model (made with the canvas)

  function rgba(colour, alpha) {
    return 'rgba(' + colour[0] + ',' + colour[1] + ',' + colour[2] + ',' + Math.max(0, Math.min(1, alpha)).toFixed(3) + ')';
  }

  function makeOrb() {
    var orb = make('div', '');
    orb.id = 'orb';
    orb.setAttribute('role', 'img');
    orb.setAttribute('aria-label', CANVAS_WORDS.orb);
    var pic = document.createElement('canvas');
    pic.className = 'o-pic';
    pic.setAttribute('aria-hidden', 'true');
    orb.appendChild(pic);
    ORB = {
      state: 'idle', muted: false, offline: false, dim: false, away: false, form: '',
      lvl: 0, lvlTo: 0, voice: 0, voiceTo: 0,
      flat: 0, flatV: 0, flatTo: 0,            // 0: the rings' pose .. 1: facing (a spring: lively turns a little past)
      shut: 0, shutV: 0, shutTo: 0,            // 0: each ring at its depth .. 1: all in one plane (they click shut)
      leanX: 0, leanXV: 0, leanY: 0, leanYV: 0,
      spin: {far: 0, need: 0, ticks: 0, arc: 0, main: 0, inner: 0, front: 0, front2: 0},
      rate: {ticks: 1, arc: 1, front: 1, front2: 1},
      x: 0, y: 0, s: 1,                        // where its centre is (from the resting place) and its size
      trip: null,                              // the way to or from the corner: {from, to, began, ms, lively}
      ghosts: [],                              // the trail: [{x, y, s, a}] while it travels
      click: -1, born: -1, clock: 0, last: 0, frame: 0, raf: 0, parity: 0,
      q: 1, slow: 0, timed: 0,                 // the drawing's own resolution (lowered when frames come late)
      box: null, fit: 1, cut: null, sprites: {}
    };
    return {orb: orb, pic: pic, ctx: pic.getContext('2d')};
  }

  // ---- where it is ------------------------------------------------------------------------------------------------

  function orbPlaces() {                       // the orb's centre, from its resting place: the centre, the corner
    var w = window.innerWidth, h = window.innerHeight;
    var fit = clamp(Math.min((h * 0.71) / STAGE, (w * 0.62) / STAGE), 0.5, 1.15);
    return {
      fit: fit, rest: {x: w / 2, y: h * 0.478},
      centre: {x: 0, y: 0, s: 1},
      corner: {x: (w - CORNER_INSET) - w / 2, y: (h - CORNER_INSET) - h * 0.478,
               s: CORNER_SCALE * clamp(fit, 0.8, 1) / fit}
    };
  }

  function orbPlace(form) {
    var places = orbPlaces();
    return form === 'corner' ? places.corner : places.centre;
  }

  function boxAround(points, fit) {            // the canvas's own box in the page: around every place the orb is drawn at
    var left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
    points.forEach(function (point) {
      var wide = REACH_X * fit * point.s / 2 + 2, tall = REACH_Y * fit * point.s / 2 + 2;
      left = Math.min(left, point.x - wide);
      top = Math.min(top, point.y - tall);
      right = Math.max(right, point.x + wide);
      bottom = Math.max(bottom, point.y + tall);
    });
    return {x: Math.floor(left), y: Math.floor(top), w: Math.ceil(right - left), h: Math.ceil(bottom - top)};
  }

  function setOrbBox(box) {                    // the canvas placed and sized (its pixels at the drawing's resolution)
    var c = canvas, o = ORB;
    var same = o.box && o.box.x === box.x && o.box.y === box.y && o.box.w === box.w && o.box.h === box.h
      && o.box.q === o.q;
    if (same) {
      return;
    }
    o.box = {x: box.x, y: box.y, w: box.w, h: box.h, q: o.q};
    c.pic.style.left = box.x + 'px';
    c.pic.style.top = box.y + 'px';
    c.pic.style.width = box.w + 'px';
    c.pic.style.height = box.h + 'px';
    c.pic.width = Math.max(1, Math.round(box.w * o.q));
    c.pic.height = Math.max(1, Math.round(box.h * o.q));
  }

  function placeCanvas() {                     // on a resize, a new form, a new row of buttons
    var c = canvas;
    if (!c) {
      return;
    }
    // The board's box starts under the buttons' rows (two at a narrow window).
    var top = Math.max(64, Math.ceil(c.tr.getBoundingClientRect().bottom) + 14);
    document.documentElement.style.setProperty('--board-top', top + 'px');
    var places = orbPlaces(), o = ORB;
    if (o.fit !== places.fit) {
      o.fit = places.fit;
      o.sprites = {};                          // the bloom is painted again at the new size
    }
    if (!o.trip) {
      var place = orbPlace(o.form);
      o.x = place.x;
      o.y = place.y;
      o.s = place.s;
      setOrbBox(boxAround([{x: places.rest.x + o.x, y: places.rest.y + o.y, s: o.s}], o.fit));
    }
    wake();
  }

  // ---- its moves ----------------------------------------------------------------------------------------------------

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function easeOutBack(t) {                    // past its end by a little, then back
    var c1 = 1.15, c3 = c1 + 1;
    return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
  }

  function tripAt(trip, own) {                 // where the orb is on its way, ``own`` 0..1 of it: a curve (out to the
    var places = orbPlaces();                  // side first, then down - back the same way); lively: a little past its
    var ctrl = {x: places.corner.x * 0.94, y: places.centre.y - 26};    // end and back, its size settling with a bounce
    var from = trip.from, to = trip.to;
    var u = trip.lively ? easeOutBack(own) : easeOutCubic(own);
    var s = from.s + (to.s - from.s) * easeOutCubic(clamp(own / 0.8, 0, 1));
    if (trip.lively && own > 0.8) {
      var tail = (own - 0.8) / 0.2;
      s *= 1 + 0.085 * Math.sin(2 * Math.PI * tail) * (1 - tail);
    }
    return {x: (1 - u) * (1 - u) * from.x + 2 * (1 - u) * u * ctrl.x + u * u * to.x,
            y: (1 - u) * (1 - u) * from.y + 2 * (1 - u) * u * ctrl.y + u * u * to.y, s: s};
  }

  function setForm(form) {
    var c = canvas, o = ORB, was = o.form;
    if (form === was) {
      return;
    }
    var from = {x: o.x, y: o.y, s: o.s}, to = orbPlace(form);
    var flat = form !== 'rings';
    o.form = c.form = form;
    o.flatTo = o.shutTo = flat ? 1 : 0;
    c.orb.classList.toggle('flat', flat);
    c.orb.classList.toggle('cornered', form === 'corner');
    var now = performance.now();
    if (!was) {                                // the page came up: the orb assembles, once (off: it is there)
      o.flat = o.flatTo;
      o.shut = K === 0 ? o.shutTo : 1;
      o.born = K === 0 ? -1 : now;
      placeCanvas();
      return;
    }
    if (K === 0) {                             // off: a cut and a short fade
      o.flat = o.flatTo;
      o.shut = o.shutTo;
      o.flatV = o.shutV = 0;
      if (o.trip) {
        o.q = o.trip.q;
      }
      o.trip = null;
      o.ghosts = [];
      placeCanvas();
      if (c.pic.animate) {
        c.pic.animate([{opacity: 0.2}, {opacity: 1}], {duration: CUT_FADE_MS});
      }
      return;
    }
    if ((was === 'corner') !== (form === 'corner')) {
      var places = orbPlaces();
      o.trip = {from: from, to: to, began: now, ms: TRIP_MS * K, lively: motion === 'lively', q: o.q};
      o.q = Math.min(o.q, 0.75);               // its canvas is large and it is moving: drawn a little coarser
      setOrbBox(boxAround([{x: places.rest.x + from.x, y: places.rest.y + from.y, s: Math.max(from.s, 1.1 * to.s)},
                           {x: places.rest.x + to.x, y: places.rest.y + to.y, s: Math.max(to.s, from.s * 0.4) * 1.12},
                           {x: places.rest.x + places.corner.x * 0.94, y: places.rest.y - 26, s: 0.7}], o.fit));
    }
    wake();
  }

  function spring(value, speed, target, stiffness, damping, dt) {
    var force = (target - value) * stiffness - speed * damping;
    speed += force * dt;
    return [value + speed * dt, speed];
  }

  function stepOrb(now, dt) {                  // the model, ``dt`` seconds on
    var o = ORB, lively = motion === 'lively';
    var slow = K > 1 ? K : 1;                  // calm: 1.6 times slower, and no overshoot
    o.clock += dt / slow;
    // the turn to face (and back): a spring - lively turns a little past and settles; calm does not
    var stiff = 150 / (slow * slow), damp = 2 * Math.sqrt(stiff) * (lively ? 0.52 : 1);
    var turned = spring(o.flat, o.flatV, o.flatTo, stiff, damp, dt);
    o.flat = turned[0];
    o.flatV = turned[1];
    // the rings along the axis: they close late and fast, a touch through, and click shut; they spring open
    var closing = o.shutTo === 1, due = closing ? o.flat > 0.45 : true;
    if (due) {
      var hard = (closing ? 420 : 170) / (slow * slow), ease = 2 * Math.sqrt(hard) * (lively ? 0.46 : 1);
      var before = o.shut;
      var shutting = spring(o.shut, o.shutV, o.shutTo, hard, ease, dt);
      o.shut = shutting[0];
      o.shutV = shutting[1];
      if (closing && before < 1 && o.shut >= 1 && lively && o.click < 0) {
        o.click = now;                         // the click: one small beat of the whole badge
      }
    }
    if (!closing) {
      o.click = -1;
    }
    // the lean a state adds
    var lean = LEANS[o.state] || [0, 0], hold = 60 / (slow * slow), give = 2 * Math.sqrt(hold) * (lively ? 0.6 : 1);
    var leanX = spring(o.leanX, o.leanXV, o.flatTo ? 0 : lean[0], hold, give, dt);
    var leanY = spring(o.leanY, o.leanYV, o.flatTo ? 0 : lean[1], hold, give, dt);
    o.leanX = leanX[0];
    o.leanXV = leanX[1];
    o.leanY = leanY[0];
    o.leanYV = leanY[1];
    // each ring's own turn, at its state's rate (eased, so a change of state never jumps)
    var rates = o.muted ? RATES.waiting : (RATES[o.state] || RATES.idle);
    var blend = 1 - Math.exp(-dt / 0.22);
    RINGS.forEach(function (ring) {
      if (!ring.turn) {
        return;
      }
      var want = ring.name === 'need' ? 1 : (rates[ring.name] || 1);
      var rate = o.rate[ring.name] === undefined ? want : o.rate[ring.name];
      rate += (want - rate) * blend;
      o.rate[ring.name] = rate;
      o.spin[ring.name] = (o.spin[ring.name] + ring.turn * rate * dt / slow) % 360;
    });
    // the levels: the microphone's and his voice's, eased a little
    var quick = 1 - Math.exp(-dt / 0.07);
    o.lvl += (o.lvlTo - o.lvl) * quick;
    o.voice += (o.voiceTo - o.voice) * quick;
    // the trip to the corner (or back)
    if (o.trip) {
      var own = clamp((now - o.trip.began) / o.trip.ms, 0, 1);
      var at = tripAt(o.trip, own);
      o.x = at.x;
      o.y = at.y;
      o.s = at.s;
      o.ghosts = [];
      if (o.trip.lively) {
        for (var i = 1; i <= TRAIL; i += 1) {
          var lag = 0.07 * i, behind = clamp((own - lag) / (1 - lag), 0, 1);
          var ghost = tripAt(o.trip, behind);
          ghost.a = 0.4 * Math.sin(Math.PI * own) * (1 - lag * 3);
          o.ghosts.push(ghost);
        }
      }
      if (own >= 1) {
        o.q = o.trip.q;
        o.trip = null;
        o.ghosts = [];
        placeCanvas();
      }
    }
  }

  function orbBusy(now) {                      // something of it is lively: every frame is drawn (a move between
    var o = ORB;                               // forms, listening, thinking, speaking); at rest every other frame is
    return !!o.trip || (!o.muted && (o.state === 'thinking' || o.state === 'listening' || o.state === 'speaking'))
      || Math.abs(o.flat - o.flatTo) > 0.002 || Math.abs(o.flatV) > 0.01
      || Math.abs(o.shut - o.shutTo) > 0.002 || Math.abs(o.shutV) > 0.01
      || Math.abs(o.leanXV) > 0.5 || Math.abs(o.leanYV) > 0.5
      || (o.born >= 0 && now - o.born < INTRO_MS * K) || (o.click >= 0 && now - o.click < CLICK_MS * 2);
  }

  // ---- the drawing ----------------------------------------------------------------------------------------------------

  function matrix(rx, ry, rz) {                // Rz * Ry * Rx (degrees), as CSS's rotateZ() rotateY() rotateX()
    var a = rx * Math.PI / 180, b = ry * Math.PI / 180, g = rz * Math.PI / 180;
    var ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(b), sb = Math.sin(b), cg = Math.cos(g), sg = Math.sin(g);
    return [cg * cb, cg * sb * sa - sg * ca, cg * sb * ca + sg * sa,
            sg * cb, sg * sb * sa + cg * ca, sg * sb * ca - cg * sa,
            -sb, cb * sa, cb * ca];
  }

  function times(m, n) {
    var out = [];
    for (var row = 0; row < 3; row += 1) {
      for (var col = 0; col < 3; col += 1) {
        out.push(m[row * 3] * n[col] + m[row * 3 + 1] * n[3 + col] + m[row * 3 + 2] * n[6 + col]);
      }
    }
    return out;
  }

  function orbPose() {                         // the whole pose now: the rings' own, the state's lean, the slow cone
    var o = ORB, open = clamp(1 - o.flat, -0.4, 1.2);
    var cone = (K === 0 || o.state === 'listening') ? 0 : clamp(1 - o.flat, 0, 1);
    var turn = 2 * Math.PI * o.clock / 14;
    var pose = matrix(0, POSE_Y * open, POSE_Z * open);
    var lean = matrix(o.leanX, o.leanY, 0);
    var sweep = matrix(4.5 * Math.sin(turn) * cone, 5.5 * (1 - Math.cos(turn)) * cone, -3 * (1 - Math.cos(turn)) * cone);
    return times(times(pose, lean), sweep);
  }

  function bloomSprite(key, size) {            // the soft light behind it, painted once for its size and colour
    var o = ORB, id = key + ':' + size;
    if (o.sprites[id]) {
      return o.sprites[id];
    }
    var sprite = document.createElement('canvas');
    sprite.width = sprite.height = size;
    var paint = sprite.getContext('2d'), half = size / 2;
    var inks = INKS[o.offline ? 'steel' : 'live'];
    // (the mock's gradient ends at 68 % of its 280 px: the sprite is only as large as what it lights)
    var light = paint.createRadialGradient(half, half, 0, half, half, half);
    light.addColorStop(0, rgba(key === 'amber' ? inks.amber : inks.c, key === 'amber' ? 0.2 : 0.30));
    light.addColorStop(0.62, rgba(inks.c, 0.09));
    light.addColorStop(1, rgba(inks.c, 0));
    paint.fillStyle = light;
    paint.fillRect(0, 0, size, size);
    o.sprites[id] = sprite;
    return sprite;
  }

  function haloFill(ctx, ring, inks, alpha, lift) {
    // A whole ring's glow: one fill of the band around its line with a radial gradient (smooth, as a blurred shadow
    // is), in the ring's own plane - so it is seen at the ring's angle. Made once for each colour.
    var id = ORB.offline ? 'steel' : 'live';
    ring.fills = ring.fills || {};
    var first = ring.halo[0][0], last = ring.halo[ring.halo.length - 1][0];
    var inner = ring.r + first, outer = ring.r + last;
    if (!ring.fills[id]) {
      var colour = inks[ring.haloKey || ring.key];
      var fill = ctx.createRadialGradient(0, 0, inner, 0, 0, outer);
      ring.halo.forEach(function (stop) {
        fill.addColorStop((stop[0] - first) / (last - first), rgba(colour, stop[1]));
      });
      var band = new Path2D();
      band.arc(0, 0, outer, 0, 2 * Math.PI);
      band.arc(0, 0, inner, 0, 2 * Math.PI, true);
      ring.fills[id] = {fill: fill, band: band};
    }
    ctx.globalAlpha = clamp(alpha * lift, 0, 1);
    ctx.fillStyle = ring.fills[id].fill;
    ctx.fill(ring.fills[id].band, 'evenodd');
    ctx.globalAlpha = 1;
  }

  function nameSprite(size, exact) {           // JARVIS in the display face with its glow, painted once for its size
    // (its exact size while the badge rests - a plain copy then - else the nearest tenth, drawn through the pose)
    var o = ORB, px = exact ? Math.max(0.1, size) : Math.max(0.2, Math.round(size * 10) / 10);
    var id = 'name:' + px.toFixed(4) + (o.offline ? ':steel' : '');
    if (Object.keys(o.sprites).length > 12) {
      o.sprites = {};                          // (sizes on the way of a trip: never kept for good)
    }
    if (o.sprites[id]) {
      return o.sprites[id];
    }
    var inks = INKS[o.offline ? 'steel' : 'live'];
    var pic = document.createElement('canvas');
    pic.width = Math.ceil(260 * px);
    pic.height = Math.ceil(96 * px);
    var paint = pic.getContext('2d');
    o.font = o.font || (token('--font-display') || 'sans-serif');
    paint.font = '600 ' + (37 * px).toFixed(2) + 'px ' + o.font;
    paint.textAlign = 'center';
    paint.textBaseline = 'middle';
    try {
      paint.letterSpacing = (3.3 * px).toFixed(2) + 'px';
    } catch (ignored) {
      // an engine without it: the name with its own spacing
    }
    paint.fillStyle = '#fff';
    [[34 * px, 0.8, inks.c], [14 * px, 0.95, inks.hi]].forEach(function (glow) {   // the mock's two shadows
      paint.shadowColor = rgba(glow[2], glow[1]);
      paint.shadowBlur = glow[0];
      paint.fillText(CANVAS_WORDS.name, pic.width / 2 + 1.6 * px, pic.height / 2 + px);
    });
    paint.shadowBlur = 0;
    paint.shadowColor = 'rgba(0,0,0,0)';
    paint.fillText(CANVAS_WORDS.name, pic.width / 2 + 1.6 * px, pic.height / 2 + px);
    o.sprites[id] = {pic: pic, px: px};
    return o.sprites[id];
  }

  function glowStroke(ctx, path, ring, colour, alpha, lift, glow) {
    // An arc's line, and its glow under it: ONE fill of the band around the line (a sector of a ring) with a radial
    // gradient, and a soft blob at each of its ends - wide strokes laid over each other cost several times more.
    if (ring.glow) {
      var id = ORB.offline ? 'steel' : 'live', reach = ring.glow * 1.15, rad = Math.PI / 180;
      ring.fills = ring.fills || {};
      if (!ring.fills[id]) {
        var ink = glow || colour, peak = 0.34 * ring.power;
        var fill = ctx.createRadialGradient(0, 0, ring.r - reach, 0, 0, ring.r + reach);
        [[0, 0], [0.22, 0.1], [0.38, 0.42], [0.5, 1], [0.62, 0.42], [0.78, 0.1], [1, 0]].forEach(function (stop) {
          fill.addColorStop(stop[0], rgba(ink, stop[1] * peak));
        });
        var band = new Path2D(), ends = [];
        (ring.arcs || [[0, 360]]).forEach(function (arc) {
          band.moveTo(Math.cos(arc[0] * rad) * (ring.r + reach), Math.sin(arc[0] * rad) * (ring.r + reach));
          band.arc(0, 0, ring.r + reach, arc[0] * rad, arc[1] * rad);
          band.arc(0, 0, ring.r - reach, arc[1] * rad, arc[0] * rad, true);
          band.closePath();
          ends.push([Math.cos(arc[0] * rad) * ring.r, Math.sin(arc[0] * rad) * ring.r],
                    [Math.cos(arc[1] * rad) * ring.r, Math.sin(arc[1] * rad) * ring.r]);
        });
        var blob = ctx.createRadialGradient(0, 0, 0, 0, 0, reach);
        blob.addColorStop(0, rgba(ink, 0.55 * peak));
        blob.addColorStop(0.45, rgba(ink, 0.2 * peak));
        blob.addColorStop(1, rgba(ink, 0));
        ring.fills[id] = {fill: fill, band: band, ends: ends, blob: blob, reach: reach};
      }
      var made = ring.fills[id];
      ctx.globalAlpha = clamp(alpha * lift, 0, 1);
      ctx.fillStyle = made.fill;
      ctx.fill(made.band);
      ctx.fillStyle = made.blob;
      made.ends.forEach(function (end) {
        ctx.translate(end[0], end[1]);
        ctx.beginPath();
        ctx.arc(0, 0, made.reach, 0, 2 * Math.PI);
        ctx.fill();
        ctx.translate(-end[0], -end[1]);
      });
      ctx.globalAlpha = 1;
    }
    ctx.lineWidth = ring.w;
    ctx.strokeStyle = rgba(colour, (ring.alpha || 1) * alpha);
    ctx.stroke(path);
  }

  function ringPath(ring) {                    // a ring's own line, in its own plane (made once)
    if (ring.path) {
      return ring.path;
    }
    var path = new Path2D(), rad = Math.PI / 180;
    if (ring.ticks) {
      for (var i = 0; i < ring.ticks; i += 1) {
        var angle = 2 * Math.PI * i / ring.ticks;
        path.moveTo(Math.cos(angle) * (ring.r - 6), Math.sin(angle) * (ring.r - 6));
        path.lineTo(Math.cos(angle) * (ring.r + 6), Math.sin(angle) * (ring.r + 6));
      }
    } else {
      (ring.arcs || [[0, 360]]).forEach(function (arc) {
        path.moveTo(Math.cos(arc[0] * rad) * ring.r, Math.sin(arc[0] * rad) * ring.r);
        path.arc(0, 0, ring.r, arc[0] * rad, arc[1] * rad);
      });
    }
    ring.path = path;
    return path;
  }

  function drawOrbAt(ctx, cx, cy, size, now) { // the whole orb, its centre at (cx, cy), ``size`` px a mock px
    var o = ORB, inks = INKS[o.offline ? 'steel' : 'live'];
    var m = orbPose(), open = 1 - o.shut;
    var dimmed = (o.muted ? 0.38 : 1) * (o.dim ? 0.7 : 1);
    var born = o.born >= 0 ? clamp((now - o.born) / (INTRO_MS * K || 1), 0, 1) : 1;
    var grown = 0.86 + 0.14 * easeOutCubic(born);
    var beat = 0;
    if (o.click >= 0) {
      var since = (now - o.click) / CLICK_MS;
      beat = since < 2 ? Math.sin(Math.PI * clamp(since, 0, 1)) * (since < 1 ? 1 : 0) : 0;
    }
    var scale = size * grown * (1 + 0.05 * beat);
    var breath = K === 0 ? 0.5 : 0.5 + 0.5 * Math.sin(2 * Math.PI * o.clock / 2.6);
    var slowBreath = K === 0 ? 0.5 : 0.5 + 0.5 * Math.sin(2 * Math.PI * o.clock / 5.2);
    var flatness = clamp(o.flat, 0, 1);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    // the soft light behind it
    var bloomSize = Math.max(8, Math.round(382 * size));
    var bloom = bloomSprite(o.state === 'needs' && !o.muted ? 'amber' : 'c', bloomSize);
    ctx.globalAlpha = clamp(easeOutCubic(born) * dimmed * (0.72 + 0.2 * slowBreath + 0.3 * beat), 0, 1);
    ctx.drawImage(bloom, Math.round(cx - bloomSize / 2), Math.round(cy - bloomSize / 2));
    ctx.globalAlpha = easeOutCubic(born);
    ctx.lineCap = 'butt';
    // the axis's own line, and the parts along it, back to front
    var axisAlpha = clamp(1 - flatness * 1.7, 0, 1) * dimmed;
    if (o.state === 'thinking' && K > 0 && !o.muted) {     // thinking: the axis flickers
      var tick = Math.floor(o.clock * 17);
      axisAlpha *= [1, 1, 0.3, 1, 1, 0.55, 1, 0.2, 0.85, 1, 0.45, 1, 1][tick % 13];
    }
    function along(z) {                        // a point on the axis, on the canvas, and its size there
      var depth = m[8] * z, near = PERSPECTIVE / (PERSPECTIVE - depth * size);
      return {x: cx + m[2] * z * scale * near, y: cy + m[5] * z * scale * near, near: near};
    }
    function axisPiece(from, to) {
      if (axisAlpha <= 0.01) {
        return;
      }
      var a = along(from), b = along(to);
      var fade = function (z) { return clamp((380 - Math.abs(z)) / 296, 0, 1); };
      var mid = function (z) { return Math.abs(z) < 60 ? inks.white : inks.c; };
      var line = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
      line.addColorStop(0, rgba(mid(from), fade(from) * axisAlpha));
      line.addColorStop(1, rgba(mid(to), fade(to) * axisAlpha));
      ctx.lineWidth = Math.max(1, 1.8 * scale);
      ctx.strokeStyle = line;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    if (axisAlpha > 0.01) {                    // the axis's glow, once, under everything on it
      var tail = along(-380), head = along(380);
      [[20 * scale, 0.07], [10 * scale, 0.15], [4.5 * scale, 0.34]].forEach(function (pass) {
        var glow = ctx.createLinearGradient(tail.x, tail.y, head.x, head.y);
        glow.addColorStop(0, rgba(inks.c, 0));
        glow.addColorStop(0.24, rgba(inks.c, pass[1] * axisAlpha));
        glow.addColorStop(0.5, rgba(inks.hi, pass[1] * axisAlpha));
        glow.addColorStop(0.76, rgba(inks.c, pass[1] * axisAlpha));
        glow.addColorStop(1, rgba(inks.c, 0));
        ctx.lineWidth = pass[0];
        ctx.strokeStyle = glow;
        ctx.beginPath();
        ctx.moveTo(tail.x, tail.y);
        ctx.lineTo(head.x, head.y);
        ctx.stroke();
      });
    }
    function core() {                          // the lit point where the axis meets the main ring
      if (axisAlpha <= 0.01) {
        return;
      }
      var radius = 78 * scale, light = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      light.addColorStop(0, rgba([255, 255, 255], axisAlpha));
      light.addColorStop(0.115, rgba([255, 255, 255], axisAlpha));
      light.addColorStop(0.17, rgba(inks.hi, 0.9 * axisAlpha));
      light.addColorStop(0.3, rgba(inks.hi, 0.5 * axisAlpha));
      light.addColorStop(0.55, rgba(inks.c, 0.2 * axisAlpha));
      light.addColorStop(1, rgba(inks.c, 0));
      ctx.fillStyle = light;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, 2 * Math.PI);
      ctx.fill();
    }
    var reach = -380;
    RINGS.forEach(function (ring) {
      var z = ring.z * open * (o.born >= 0 ? easeOutCubic(born) : 1);
      if (z > reach) {
        axisPiece(reach, Math.min(z, 380));
        reach = z;
      }
      if (ring.name === 'front') {
        core();
      }
      var alpha = dimmed, lift = 1, grow = 1;
      if (ring.name === 'need') {
        alpha = o.state === 'needs' && !o.muted ? dimmed : 0;
      } else if (ring.name === 'ticks') {
        if (o.state === 'listening') {         // the tick ring follows the microphone
          grow = 1 + o.lvl * 0.13;
          alpha *= 0.55 + 0.45 * o.lvl;
        } else if (o.state === 'waiting' && K > 0) {
          alpha *= 0.45 + 0.55 * slowBreath;
        }
      } else if (ring.name === 'main') {
        if (o.state === 'speaking') {          // the main ring pulses with his voice
          grow = 1 + o.voice * 0.055;
          lift = 0.75 + 0.85 * o.voice;
        } else {
          lift = 1 + 0.5 * breath;               // the breath
        }
        lift += 0.5 * beat;
      }
      if (alpha <= 0.01) {
        return;
      }
      var centre = along(z), k = scale * centre.near * grow;
      ctx.setTransform(m[0] * k, m[3] * k, m[1] * k, m[4] * k, centre.x, centre.y);
      ctx.rotate(o.spin[ring.name] * Math.PI / 180);
      if (ring.halo) {
        haloFill(ctx, ring, inks, alpha, ring.name === 'main' ? 0.7 * lift : 1);
        ctx.lineWidth = ring.w;
        ctx.strokeStyle = rgba(inks[ring.key], alpha);
        ctx.stroke(ringPath(ring));
      } else {
        ctx.setLineDash(ring.dash || []);
        glowStroke(ctx, ringPath(ring), ring, inks[ring.key], alpha, lift, ring.haloKey ? inks[ring.haloKey] : null);
        ctx.setLineDash([]);
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    });
    axisPiece(reach, 380);
    // the name, in the badge (it turns with it)
    var named = clamp((o.shut - 0.72) / 0.28, 0, 1) * (o.flatTo ? 1 : clamp(o.flat * 2 - 1, 0, 1)) * (o.muted ? 0.6 : 1)
      * (o.dim ? 0.7 : 1);
    if (named > 0.01) {
      var name = nameSprite(size, !o.trip && o.flat === o.flatTo);
      var facing = Math.abs(m[0] - 1) < 0.002 && Math.abs(m[4] - 1) < 0.002 && Math.abs(m[1]) < 0.002
        && Math.abs(scale - size) < 0.002 * size;
      ctx.globalAlpha = named * easeOutCubic(born);
      if (facing && name.px === size) {        // facing, at its own size: a plain copy (an image through a transform
        ctx.drawImage(name.pic, Math.round(cx - name.pic.width / 2), Math.round(cy - name.pic.height / 2));   // costs)
      } else {
        var per = 1 / name.px;                 // the sprite's pixels in the badge's own units
        ctx.setTransform(m[0] * scale * per, m[3] * scale * per, m[1] * scale * per, m[4] * scale * per, cx, cy);
        ctx.drawImage(name.pic, -name.pic.width / 2, -name.pic.height / 2);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
      }
    }
    // muted: a slash across it
    if (o.muted) {
      ctx.globalAlpha = 0.9 * easeOutCubic(born);
      ctx.setTransform(m[0] * scale, m[3] * scale, m[1] * scale, m[4] * scale, cx, cy);
      ctx.rotate(-Math.PI / 4);
      [[10, 0.18], [3, 1]].forEach(function (pass) {
        ctx.lineWidth = pass[0];
        ctx.strokeStyle = rgba(inks.white, pass[1]);
        ctx.beginPath();
        ctx.moveTo(-180, 0);
        ctx.lineTo(180, 0);
        ctx.stroke();
      });
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    }
    ctx.globalAlpha = 1;
  }

  function drawGhost(ctx, cx, cy, size, alpha) {      // a faint copy of the main ring, on the orb's way
    var inks = INKS[ORB.offline ? 'steel' : 'live'];
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    [[22 * size, 0.2], [6 * size, 0.8]].forEach(function (pass) {
      ctx.lineWidth = pass[0];
      ctx.strokeStyle = rgba(inks.c, pass[1] * alpha);
      ctx.beginPath();
      ctx.arc(cx, cy, 131 * size, 0, 2 * Math.PI);
      ctx.stroke();
    });
  }

  function drawOrb(now) {
    var c = canvas, o = ORB, box = o.box;
    if (!box) {
      return;
    }
    var ctx = c.ctx, places = orbPlaces(), q = o.q;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.clearRect(0, 0, c.pic.width, c.pic.height);
    var size = o.fit * q;
    o.ghosts.forEach(function (ghost) {
      drawGhost(ctx, (places.rest.x + ghost.x - box.x) * q, (places.rest.y + ghost.y - box.y) * q, size * ghost.s, ghost.a);
    });
    drawOrbAt(ctx, (places.rest.x + o.x - box.x) * q, (places.rest.y + o.y - box.y) * q, size * o.s, now);
    o.frame += 1;
  }

  function tickOrb(now) {                      // one frame: the model a step on, then the drawing
    var o = ORB;
    o.raf = -1;                                // in the frame: wake() asks for nothing (the frame itself goes on)
    if (!canvas || document.hidden) {
      o.last = 0;
      o.raf = 0;
      return;
    }
    var dt = o.last ? Math.min(0.05, (now - o.last) / 1000) : 0.016;
    var gap = o.last ? now - o.last : 16;
    o.last = now;
    if (K === 0) {
      stepOrb(now, 0);
      o.lvl = o.lvlTo;
      o.voice = o.voiceTo;
      drawOrb(now);
      o.raf = 0;
      return;                                  // off: drawn when something changes, never in a loop
    }
    stepOrb(now, dt);
    var busy = orbBusy(now);
    // At rest its moves are slow: every other frame is enough - every sixth while Jarvis's window is not the one the
    // owner works in (the app says so: "away"); a move or an active state draws every frame.
    var every = busy ? 1 : (o.away ? 6 : 2);
    o.parity = (o.parity + 1) % every;
    if (o.parity === 0) {
      drawOrb(now);
    }
    // Frames that come late for a while (a busy PC, a very large window): the drawing's resolution steps down, once
    // or twice, so what is lively stays at the screen's own rate (it is a little softer then, never slower).
    if (busy && !o.trip) {
      o.timed += 1;
      o.slow += gap > 21 ? 1 : 0;
      if (o.timed >= 90) {
        if (o.slow >= 14 && o.q > 0.6) {
          o.q = o.q > 0.85 ? 0.8 : 0.64;
          if (!o.trip) {
            placeCanvas();
          }
        }
        o.timed = o.slow = 0;
      }
    }
    o.raf = requestAnimationFrame(tickOrb);
  }

  function wake() {                            // something changed: the next frame is drawn
    var o = ORB;
    if (canvas && o && o.raf === 0) {
      o.raf = requestAnimationFrame(tickOrb);
    }
  }

  function orbMotionChanged() {                // the motion level changed: lively, calm (slower, no overshoot), off (still)
    var o = ORB;
    if (!o) {
      return;
    }
    if (K === 0) {
      o.flat = o.flatTo;
      o.shut = o.shutTo;
      o.flatV = o.shutV = o.leanXV = o.leanYV = 0;
      var lean = LEANS[o.state] || [0, 0];
      o.leanX = o.flatTo ? 0 : lean[0];
      o.leanY = o.flatTo ? 0 : lean[1];
      if (o.trip) {
        o.q = o.trip.q;
      }
      o.trip = null;
      o.ghosts = [];
      o.born = o.click = -1;
      placeCanvas();
    }
    wake();
  }

  function orbLook() {                         // the orb as it is now, in numbers (the probes' own word on it)
    var o = ORB;
    if (!o) {
      return null;
    }
    var places = orbPlaces(), m = orbPose(), now = performance.now();
    var round = function (value) { return Math.round(value * 1000) / 1000; };
    return {
      state: o.state, form: o.form, muted: o.muted, offline: o.offline, dim: o.dim, away: o.away,
      flat: round(o.flat), shut: round(o.shut), tilt: round(Math.acos(clamp(m[8], -1, 1)) * 180 / Math.PI),
      lean: [round(o.leanX), round(o.leanY)], lvl: round(o.lvl), voice: round(o.voice),
      centre: [round(places.rest.x + o.x), round(places.rest.y + o.y)], size: round(o.fit * o.s),
      main: round(2 * 131 * o.fit * o.s), travelling: !!o.trip, ghosts: o.ghosts.length,
      name: round(clamp((o.shut - 0.72) / 0.28, 0, 1) * (o.flatTo ? 1 : clamp(o.flat * 2 - 1, 0, 1))),
      axis: round(clamp(1 - clamp(o.flat, 0, 1) * 1.7, 0, 1)),
      lively: !!(o.trip && o.trip.lively), tripMs: o.trip ? o.trip.ms : 0,
      rate: {ticks: round(o.rate.ticks), arc: round(o.rate.arc), front: round(o.rate.front),
             front2: round(o.rate.front2)},
      busy: orbBusy(now), looping: o.raf !== 0, frames: o.frame, q: o.q,
      box: o.box ? [o.box.x, o.box.y, o.box.w, o.box.h] : null, k: K
    };
  }

  function makeCanvas() {                      // once: the canvas layer under the board, its orb and its corners
    if (canvas) {
      return canvas;
    }
    var html = document.documentElement;
    var el = make('div', '');
    el.id = 'canvas';
    var parts = makeOrb();
    el.appendChild(parts.orb);

    // top left: the wordmark (the window is dragged by it), the state word, a Send's hold with its Undo, the clock
    var tl = make('div', 'c-corner c-tl');
    var wordmark = make('div', 'c-word c-drag', CANVAS_WORDS.wordmark);
    wordmark.addEventListener('mousedown', function (event) {
      if (event.button === 0) {
        post('go', {to: 'drag'});
      }
    });
    var state = make('span', 'c-mono c-state');
    state.setAttribute('aria-live', 'polite');
    var hold = make('span', 'c-mono c-hold');
    hold.hidden = true;
    var undo = canvasButton('undo', CANVAS_WORDS.undo, '');
    var clockText = make('span', 'c-mono c-clock');
    var clockButton = button('c-mono c-clock', CANVAS_WORDS.day, function () { post('go', {to: 'day'}); });
    clockButton.hidden = true;
    tl.append(wordmark, state, hold, undo, clockText, clockButton);

    // top right: History, Clear view, the folded board | the screens | the desktop HUD, the window
    var tr = make('div', 'c-corner c-tr');
    var buttons = {};
    buttons.history = canvasButton('history', CANVAS_WORDS.history, '');
    var clear = canvasButton('clear', CANVAS_WORDS.clear, '', function () { post('clear', {}); });
    clear.setAttribute('aria-label', CANVAS_WORDS.clearAll);
    buttons.board = canvasButton('board', CANVAS_WORDS.board, '');
    buttons.board.setAttribute('aria-label', CANVAS_WORDS.boardBack);
    // part 2: Tidy (only while there is something to tidy) and Pinned (a filter, only while something is pinned)
    var tidyButton = canvasButton('tidy', TASK_WORDS.tidy, '', function () { tidy(); });
    tidyButton.setAttribute('aria-label', TASK_WORDS.tidyAll);
    var pinnedButton = canvasButton('pinned', TASK_WORDS.pinned, '', function () {
      setFilter(filter === 'pinned' ? 'all' : 'pinned');
    });
    var rule1 = make('div', 'c-rule'), rule2 = make('div', 'c-rule');
    tr.append(buttons.history, tidyButton, clear, pinnedButton, buttons.board, rule1);
    SCREEN_BUTTONS.forEach(function (word) {
      buttons[word] = canvasButton(word, CANVAS_WORDS[word], '');
      tr.appendChild(buttons[word]);
    });
    buttons.desktop = canvasButton('desktop', CANVAS_WORDS.desktop, '');
    buttons.desktop.setAttribute('aria-label', CANVAS_WORDS.desktopSwitch);
    buttons.minimize = iconButton('minimize', 'minimize', CANVAS_WORDS.minimize);
    buttons.maximize = iconButton('maximize', 'window', CANVAS_WORDS.maximize);
    buttons.close = iconButton('close', 'close', CANVAS_WORDS.close);
    var windowGroup = make('div', 'c-group');     // the switch and the window's own three stay together on a row
    windowGroup.append(rule2, buttons.desktop, buttons.minimize, buttons.maximize, buttons.close);
    tr.appendChild(windowGroup);

    // bottom left: the microphone's level and Mute, "Hey Jarvis", REC and its Stop
    var bl = make('div', 'c-corner c-bl');
    var meter = make('div', 'c-meter');
    meter.setAttribute('aria-hidden', 'true');
    meter.hidden = true;
    var bars = [];
    for (var b = 0; b < METER_BARS; b += 1) {
      var bar = make('i', '');
      bars.push(bar);
      meter.appendChild(bar);
    }
    var levelWords = make('span', 'c-mono');
    levelWords.hidden = true;
    buttons.mute = canvasButton('mute', CANVAS_WORDS.mute, '');
    var wake = make('span', 'c-mono c-state');
    wake.hidden = true;
    var rec = make('span', 'c-mono c-rec');
    rec.hidden = true;
    var stopRec = canvasButton('stop_rec', CANVAS_WORDS.stop, 'need');
    bl.append(meter, levelWords, buttons.mute, wake, rec, stopRec);
    el.append(tl, tr, bl);

    canvas = {
      el: el, orb: parts.orb, pic: parts.pic, ctx: parts.ctx, tl: tl, tr: tr, bl: bl,
      state: state, hold: hold, undo: undo, clockText: clockText, clockButton: clockButton, buttons: buttons,
      clear: clear, tidy: tidyButton, pinned: pinnedButton, rules: [rule1, rule2], meter: meter, bars: bars,
      levelWords: levelWords, wake: wake, rec: rec,
      stopRec: stopRec, form: '', orbState: '', muted: false, level: -1, hasMeter: false, shown: []
    };
    html.classList.add('canvas');
    html.classList.toggle('board-off', !deskOn);
    document.body.insertBefore(el, document.body.firstChild);    // first: the board is drawn over it
    watchCanvasPointer();
    document.addEventListener('visibilitychange', wake);     // seen again: it moves again
    placeCanvas();
    if (stripParts) {                          // (nothing is "above" on the canvas: a parked answer is just new)
      var pillWords = stripParts.pill.querySelector('.pill-words');
      if (pillWords) {
        pillWords.textContent = TASK_WORDS.newAnswer;
      }
    }
    schedulePack('full');                      // frames that came before it take their places on the grid
    return canvas;
  }

  function setLevels(level, voice) {
    var c = canvas;
    ORB.lvlTo = level / 100;                   // the tick ring and the main ring follow them (eased in stepOrb)
    ORB.voiceTo = voice / 100;
    if (level === c.level) {
      return;
    }
    c.level = level;
    var lit = c.orbState === 'listening' ? Math.round(level / 100 * METER_BARS) : 0;
    c.bars.forEach(function (bar, index) {
      bar.className = index < lit ? 'on' : (index === lit && lit > 0 ? 'edge' : '');
    });
    syncLevelWords();
  }

  function syncLevelWords() {                  // "LVL −52 DB" while the microphone is open (the app's own range), else idle
    var c = canvas;
    c.levelWords.hidden = !c.hasMeter;
    if (c.orbState === 'listening' && c.level >= 0) {
      c.levelWords.textContent = CANVAS_WORDS.level + MINUS + Math.round(60 - c.level / 100 * 50) + CANVAS_WORDS.db;
    } else {
      c.levelWords.textContent = CANVAS_WORDS.micIdle;
    }
  }

  HANDLERS.orb = function (message) {
    if (!onlyKeys(message, ['type', 'state', 'form', 'muted', 'offline', 'dim', 'away', 'level', 'voice'])
        || ORB_STATES.indexOf(message.state) < 0 || ORB_FORMS.indexOf(message.form) < 0
        || typeof message.muted !== 'boolean' || typeof message.offline !== 'boolean'
        || typeof message.dim !== 'boolean' || typeof message.away !== 'boolean'
        || !whole(message.level, 0, CANVAS_CAPS.level)
        || !whole(message.voice, 0, CANVAS_CAPS.level)) {
      return drop('shape');
    }
    var c = makeCanvas();
    if (message.state !== c.orbState || message.muted !== c.muted) {
      ORB_STATES.forEach(function (name) {
        c.orb.classList.toggle('s-' + name, name === message.state);
      });
      c.orbState = message.state;
      c.muted = message.muted;
      c.buttons.mute.textContent = message.muted ? CANVAS_WORDS.unmute : CANVAS_WORDS.mute;
      c.buttons.mute.classList.toggle('on', message.muted);
      c.buttons.mute.setAttribute('aria-pressed', message.muted ? 'true' : 'false');
      c.level = -1;                            // the meter and its words follow the new state
    }
    c.orb.classList.toggle('muted', message.muted);
    c.orb.classList.toggle('offline', message.offline);
    c.orb.classList.toggle('dimmed', message.dim);
    var o = ORB;
    if (message.offline !== o.offline) {
      o.sprites = {};                          // the light behind it in the other colour
    }
    o.state = message.state;
    o.muted = message.muted;
    o.offline = message.offline;
    o.dim = message.dim;
    o.away = message.away;
    setLevels(message.level, message.voice);
    setFormSoon(message.form);
    if (K === 0) {                             // off: the state's lean at once
      var lean = LEANS[o.state] || [0, 0];
      o.leanX = o.flatTo ? 0 : lean[0];
      o.leanY = o.flatTo ? 0 : lean[1];
    }
    wake();
    return true;
  };

  HANDLERS.canvas = function (message) {
    if (!onlyKeys(message, ['type', 'clock', 'word', 'tone', 'approvals', 'buttons', 'briefing_new', 'hold', 'rec',
                            'wake', 'meter'])
        || !words(message.clock, CANVAS_CAPS.clock) || !words(message.word, CANVAS_CAPS.word)
        || CANVAS_TONES.indexOf(message.tone) < 0 || !whole(message.approvals, 0, CANVAS_CAPS.approvals)
        || !list(message.buttons, CANVAS_BUTTONS.length) || typeof message.briefing_new !== 'boolean'
        || !words(message.hold, CANVAS_CAPS.hold) || !words(message.rec, CANVAS_CAPS.rec)
        || !words(message.wake, CANVAS_CAPS.wake) || typeof message.meter !== 'boolean') {
      return drop('shape');
    }
    var shown = message.buttons;
    if (!shown.every(function (word, index) {
      return typeof word === 'string' && CANVAS_BUTTONS.indexOf(word) >= 0 && shown.indexOf(word) === index;
    })) {
      return drop('shape');
    }
    var c = makeCanvas();
    c.shown = shown.slice();
    Object.keys(c.buttons).forEach(function (word) {
      c.buttons[word].hidden = shown.indexOf(word) < 0;
    });
    c.state.textContent = message.word;
    c.state.classList.toggle('amber', message.tone === 'amber');
    c.state.classList.toggle('steel', message.tone === 'steel');
    c.state.hidden = !message.word;
    var day = shown.indexOf('day') >= 0;       // the clock opens the Day view only while that works
    c.clockText.textContent = message.clock;
    c.clockButton.textContent = message.clock;
    c.clockText.hidden = day || !message.clock;
    c.clockButton.hidden = !day || !message.clock;
    var approvals = c.buttons.approvals;
    approvals.textContent = message.approvals ? CANVAS_WORDS.approvals + ' ' + message.approvals : CANVAS_WORDS.approvals;
    approvals.classList.toggle('need', message.approvals > 0);
    approvals.setAttribute('aria-label', message.approvals
      ? CANVAS_WORDS.approvals + ', ' + message.approvals + CANVAS_WORDS.approvalsWait : CANVAS_WORDS.approvals);
    c.buttons.briefing.classList.toggle('new', message.briefing_new);
    c.buttons.briefing.setAttribute('aria-label', message.briefing_new ? CANVAS_WORDS.briefingNew : CANVAS_WORDS.briefing);
    c.hold.textContent = message.hold;
    c.hold.hidden = !message.hold;
    c.undo.hidden = !message.hold;
    c.rec.textContent = message.rec;
    c.rec.hidden = !message.rec;
    c.stopRec.hidden = !message.rec;
    c.wake.textContent = message.wake;
    c.wake.hidden = !message.wake;
    c.hasMeter = message.meter;
    c.meter.hidden = !message.meter;
    syncLevelWords();
    syncCanvasBoard();
    return true;
  };

  function syncCanvasBoard() {                 // Clear view only while the board shows frames; the rules between groups
    var c = canvas;
    if (!c) {
      return;
    }
    document.documentElement.classList.toggle('board-off', !deskOn);
    c.clear.hidden = !(deskOn && barFrames > 0);
    c.tidy.hidden = !(deskOn && lay.untidy);
    var only = filter === 'pinned';
    c.pinned.hidden = !(deskOn && (barPinned > 0 || only));
    c.pinned.textContent = barPinned ? TASK_WORDS.pinned + ' ' + barPinned : TASK_WORDS.pinned;
    c.pinned.classList.toggle('on', only);
    c.pinned.setAttribute('aria-pressed', only ? 'true' : 'false');
    c.pinned.setAttribute('aria-label', TASK_WORDS.pinnedOnly);
    c.rules[0].hidden = false;
    c.rules[1].hidden = ['desktop', 'minimize', 'maximize', 'close'].every(function (word) {
      return c.shown.indexOf(word) < 0;
    });
    placeCanvas();
  }

  function overBoard(event) {                  // the pointer is on a frame (the canvas between the frames is not
    var node = event.target;                   // the board: part 2, the frames are spread over the whole of it)
    return deskOn && !!node && !!node.closest && !!node.closest('.frame');
  }

  function inCanvasChrome(node) {              // the orb's layer and its corner buttons: not the board
    return !!canvas && !!node && canvas.el.contains(node);
  }

  function watchCanvasPointer() {              // on the canvas "the owner at the board" means over the board's box
    document.addEventListener('mousemove', function (event) {
      var over = overBoard(event);
      if (over !== reading.pointer) {
        reading.pointer = over;
        sendReading(true);
      }
    }, {passive: true});
  }

  function focusControls() {                   // F6: the first corner button that shows
    if (!canvas) {
      return false;
    }
    var stops = Array.prototype.filter.call(canvas.el.querySelectorAll('button'), function (node) {
      return !node.hidden;
    });
    if (!stops.length) {
      return false;
    }
    stops[0].focus({preventScroll: true});
    return true;
  }

  // ---- the canvas, part 2 (PLAN-canvas part 2): frames across the screen ---------------------------------------------
  // On the canvas the board's frames are not a column: this page places each one on a grid of GRID_COLS columns over
  // the whole area between the top controls and the caption's strip (<body>'s box there: board.css). The area is cut
  // into rows and each row into sub-rows ("lines"): a frame takes a row at least, and as many lines more as what it
  // holds needs (up to two or three rows; a wide table or a long timeline twice the columns), and a folded one (a
  // tile: the board's satellite, its title and its fact) one line. The grid is as roomy as what is shown allows (densities: fewer, larger cells while few frames
  // are up); when not everything fits at the finest grid, frames fold to tiles, the oldest answer's first, never a
  // pinned one while anything else can fold. The orb's corner is never given to a frame. A group's first full frame
  // carries the group's head tile - a slim strip over it, in its own place's top HEAD_H px (the task card, shrunk,
  // once its answer arrived). Nothing scrolls.
  // What stays put: a close, a bring back, a pin and an opened tile never move the other frames (keepPack); a new
  // answer, a resize, the Pinned filter, Clear view and Tidy pack everything again (canonical). "Something to tidy"
  // means: a fresh pack would place something differently than it is now.
  // Frames glide to their new places with a spring (lively), without its overshoot (calm) or cut (off).
  //
  // MEASURED (the web engine has no GPU): everything here that moves - six or twelve frames booting, all of them
  // gliding at once, Clear view's lines - holds 58 to 60 frames a second at 1920 x 1080, as long as the frames'
  // area (<body> on the canvas) has NO ground of its own: an opaque background on that box, now the whole canvas,
  // halved the rate of everything moving on or under it (board.css says so where it is cleared).

  var GRID_COLS = 12, ROW_MIN = 165, ROW_MAX = 300, SPAN_TARGET = 410, SPAN_MAX = 640, TILE_MIN = 200, TILE_MAX = 480,
      LINE_TARGET = 54;
  var SPANS = [2, 3, 4, 6, 12];
  var HEAD_H = 30, HEAD_GAP = 8;               // a group's head tile over its first frame, and the gap under it
  var PACK_MS = 30;                            // a burst of messages is placed once
  // An arriving frame boots: its outline draws itself from one corner, a scan line wipes down it, the content comes
  // in behind the line, the heading decodes, numbers count up, bars grow a little past their end (board.css has the
  // drawn part; the words and numbers are set here). Frames of one arrival boot one after another.
  var BOOT = {first: 60, stagger: 90, outline: 420, content: 260, decode: 520, count: 700, total: 1050, max: 12};
  var GLIDE_MS = 560, FOLD_MS = 260, LINE_MS = 460, LINE_STAGGER = 26, SHRINK_MS = 360;
  var DECODE_GLYPHS = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#/+';
  var COUNT_TEXT = /^([+\-−]?[$€£]?)([0-9][0-9,]*)(\.[0-9]+)?([kMBT%]?)$/;
  var TASK_STATES = ['working', 'done', 'gone'];
  var TASK_CAPS = {title: 40, words: 60, seconds: 86400, count: 99};
  var TASK_BLOCKS = 10, CARD_WIDTH = 372;
  var TASK_WORDS = {stop: 'Stop', step: ' step', steps: ' steps', source: ' source', sources: ' sources',
                    more: 'More · Enter', tidy: 'Tidy', pinned: 'Pinned', newAnswer: 'New answer',
                    tidyAll: 'Tidy: put every frame back in its place',
                    pinnedOnly: 'Show the pinned frames only'};
  var lay = {timer: 0, mode: '', cells: new Map(), heads: new Map(), m: null, untidy: false, hold: 0, formTimer: 0,
             over: 0, packs: 0, heads_made: 0, adopted: 0};
  var placed = new WeakMap();                  // an element -> the box it was last given (px, in the board's own box)
  var card = null;                             // the task card while Jarvis works (makeHead's parts)

  // ---- the grid -------------------------------------------------------------------------------------------------------

  function gridGap() {
    return root.clientWidth < 1100 ? 10 : 14;
  }

  function gridMetrics(span, rows, tight, narrow) {     // one grid: a frame ``span`` columns wide, ``rows`` rows of lines
    var w = root.clientWidth, h = root.clientHeight, gap = gridGap();
    var unit = (w + gap) / GRID_COLS;
    var rowH = (h - (rows - 1) * gap) / rows;
    var sub = clamp(Math.round((rowH + gap) / (LINE_TARGET + gap)), 2, 6) + (tight ? 1 : 0);
    var tile = span;                           // a tile is as wide as a frame (they line up), unless frames are
    if (tight || narrow || span * unit - gap > TILE_MAX) {     // wide here or room is short: the narrowest that reads
      SPANS.forEach(function (width) {
        if (width < tile && width * unit - gap >= TILE_MIN) {
          tile = Math.min(tile, width);
        }
      });
    }
    return {w: w, h: h, gap: gap, unit: unit, span: span, rows: rows, rowH: rowH, sub: sub,
            subH: (rowH - (sub - 1) * gap) / sub, tile: tile, lines: rows * sub, tight: !!tight};
  }

  function densities() {                       // the grids this size offers, the roomiest first; the last is the finest
    var w = root.clientWidth, h = root.clientHeight, gap = gridGap(), unit = (w + gap) / GRID_COLS;
    var want = SPAN_TARGET / unit;
    var base = SPANS.reduce(function (best, width) {
      return Math.abs(width - want) < Math.abs(best - want) ? width : best;
    }, SPANS[0]);
    var most = Math.max(1, Math.floor((h + gap) / (ROW_MIN + gap)));
    if (h >= 300 && most < 2) {
      most = 2;
    }
    var fewest = Math.max(1, Math.min(most, Math.ceil((h + gap) / (ROW_MAX + gap))));
    var found = [];
    SPANS.forEach(function (width) {
      if (width < base || (width > base && width * unit - gap > SPAN_MAX)) {
        return;
      }
      for (var rows = fewest; rows <= most; rows += 1) {
        found.push({span: width, rows: rows, area: (width * unit - gap) * ((h - (rows - 1) * gap) / rows)});
      }
    });
    found.sort(function (a, b) { return b.area - a.area; });
    return found;
  }

  function blockedCells(m) {                   // the orb's corner: no frame is ever placed over it
    var occ = new Uint8Array(GRID_COLS * m.lines);
    var box = root.getBoundingClientRect(), places = orbPlaces();
    var reach = STAGE * CORNER_SCALE * clamp(places.fit, 0.8, 1) * 0.36 + 16;
    var left = window.innerWidth - CORNER_INSET - reach - box.left;
    var top = window.innerHeight - CORNER_INSET - reach - box.top;
    for (var line = 0; line < m.lines; line += 1) {
      for (var col = 0; col < GRID_COLS; col += 1) {
        if ((col + 1) * m.unit - m.gap > left && (line + 1) * (m.subH + m.gap) - m.gap > top) {
          occ[line * GRID_COLS + col] = 1;
        }
      }
    }
    return occ;
  }

  function cellsFree(occ, cell) {
    for (var line = cell.cy; line < cell.cy + cell.ch; line += 1) {
      for (var col = cell.cx; col < cell.cx + cell.cw; col += 1) {
        if (occ[line * GRID_COLS + col]) {
          return false;
        }
      }
    }
    return true;
  }

  function takeCells(occ, cell) {
    for (var line = cell.cy; line < cell.cy + cell.ch; line += 1) {
      for (var col = cell.cx; col < cell.cx + cell.cw; col += 1) {
        occ[line * GRID_COLS + col] = 1;
      }
    }
  }

  function firstFit(occ, m, cw, ch, step) {    // the first free place in reading order, from the top left
    for (var cy = 0; cy + ch <= m.lines; cy += step) {
      for (var cx = 0; cx + cw <= GRID_COLS; cx += 1) {
        var cell = {cx: cx, cy: cy, cw: cw, ch: ch};
        if (cellsFree(occ, cell)) {
          takeCells(occ, cell);
          return cell;
        }
      }
    }
    return null;
  }

  // ---- what is placed: the frames shown, their sizes ---------------------------------------------------------------

  function packItems() {                       // the frames the canvas shows, in board order (closed ones too: their
    var list = [];                             // row keeps their place until the app takes them away)
    Array.prototype.forEach.call(root.querySelectorAll('.frame'), function (node) {
      var rec = records.get(node);
      if (current(rec) && !rec.away && !parked(rec) && !(rec.group && !groups.has(rec.group))) {
        list.push(rec);
      }
    });
    return list;
  }

  function isWideKind(rec) {                   // a wide table, a long timeline (its nodes in one row): twice the columns
    var data = rec.data, body = data && data.body;
    if (!body || data.status !== 'ok') {
      return false;
    }
    return (rec.kind === 'table' && body.columns.length >= 4) || (rec.kind === 'timeline' && body.nodes.length > 4);
  }

  function frameCols(rec, m) {
    return isWideKind(rec) ? Math.min(GRID_COLS, m.span * 2) : m.span;
  }

  function colsPx(cols, m) {
    return Math.round(cols * m.unit - m.gap);
  }

  function measure(list, m) {                  // each frame's own height at the width its place would have; what it
    var todo = list.filter(function (rec) {    // looks like now is put back at once (nothing is painted in between)
      var px = colsPx(frameCols(rec, m), m);
      if (!rec.nat || rec.nat.rev !== rec.rev) {
        rec.nat = {rev: rec.rev, sizes: {}};
      }
      return !rec.closed && rec.nat.sizes[px] === undefined;
    });
    if (!todo.length) {
      return;
    }
    var was = todo.map(function (rec) {
      var el = rec.el, cols = frameCols(rec, m);
      var keep = {w: el.style.width, h: el.style.height, sat: el.classList.contains('sat'),
                  wide: el.classList.contains('wide'), cut: el.classList.contains('cut')};
      el.classList.add('measuring');
      el.classList.remove('sat', 'cut');
      el.classList.toggle('wide', cols > m.span);
      el.style.width = colsPx(cols, m) + 'px';
      el.style.height = 'auto';
      return keep;
    });
    todo.forEach(function (rec) {
      rec.nat.sizes[colsPx(frameCols(rec, m), m)] = rec.el.offsetHeight;
    });
    todo.forEach(function (rec, index) {
      var el = rec.el, keep = was[index];
      el.classList.remove('measuring');
      el.classList.toggle('sat', keep.sat);
      el.classList.toggle('wide', keep.wide);
      el.classList.toggle('cut', keep.cut);
      el.style.width = keep.w;
      el.style.height = keep.h;
    });
  }

  function frameLines(rec, m, headed) {        // the lines a frame takes: a row at least, more for what it holds
    var most = Math.min(m.rows, m.rows >= 4 ? 3 : 2) * m.sub;      // (its group's head over it counted)
    if (rec.kind === 'picture' && rec.data && rec.data.status === 'ok') {
      return Math.min(m.rows, 2) * m.sub;      // a page picture: two rows, the picture filling them
    }
    var need = (rec.nat && rec.nat.sizes[colsPx(frameCols(rec, m), m)]) || 0;
    if (headed) {
      need += HEAD_H + HEAD_GAP;
    }
    return clamp(Math.ceil((need + m.gap - 1) / (m.subH + m.gap)), m.sub, most);
  }

  function foldOrder(list, reset) {            // who folds first: the oldest answer's frames, each from its end; one
    var plain = [], opened = [], pinned = [];  // the owner opened after them; a pinned one only when nothing else can
    list.slice().reverse().forEach(function (rec) {
      if (rec.closed) {
        return;
      }
      if (rec.pinned) {
        pinned.push(rec);
      } else if (rec.expanded && !reset) {
        opened.push(rec);
      } else {
        plain.push(rec);
      }
    });
    return plain.concat(opened, pinned);
  }

  // ---- the pack -----------------------------------------------------------------------------------------------------

  function tryPack(list, m, folded, withCard, lenient) {
    var occ = blockedCells(m), cells = new Map(), heads = new Map(), headed = new Set(), ok = true, over = 0;
    if (withCard) {
      var spot = firstFit(occ, m, m.span, m.sub, 1);
      if (!spot) {
        return null;
      }
      spot.kind = 'card';
      cells.set(card.el, spot);
    }
    list.forEach(function (rec) {              // first the frames, each on whole rows
      if (!ok || rec.closed || folded.has(rec)) {
        return;
      }
      var head = !!rec.group && !headed.has(rec.group);
      var cols = frameCols(rec, m);
      var cell = firstFit(occ, m, cols, frameLines(rec, m, head), 1);
      if (!cell) {
        ok = false;
        return;
      }
      if (head) {                              // its group's head tile takes the top of its place
        headed.add(rec.group);
        heads.set(rec.group, rec.el);
        cell.top = HEAD_H + HEAD_GAP;
      }
      cell.kind = 'full';
      cells.set(rec.el, cell);
    });
    if (!ok) {
      return null;
    }
    list.forEach(function (rec) {              // then the tiles, into what is left
      if (!(rec.closed || folded.has(rec))) {
        return;
      }
      var cell = ok || lenient ? firstFit(occ, m, m.tile, 1, 1) : null;
      if (!cell) {
        ok = false;
        over += 1;
        return;
      }
      cell.kind = 'tile';
      cells.set(rec.el, cell);
    });
    return ok || lenient ? {m: m, cells: cells, heads: heads, over: over} : null;
  }

  function canonical(list, withCard, reset) {  // a fresh pack: the roomiest grid that holds everything; else the
    var none = new Set(), options = densities(), found = null, m = null;   // finest, the oldest folded until it does
    for (var index = 0; index < options.length; index += 1) {
      m = gridMetrics(options[index].span, options[index].rows, false);
      measure(list, m);
      found = tryPack(list, m, none, withCard, false);
      if (found) {
        return found;
      }
    }
    // Not everything fits whole: the finest grid, the oldest folded one by one until it does - with tiles as wide
    // as the frames, with narrow ones, then with every line a little lower; the way that folds the fewest is taken.
    var finest = options[options.length - 1], order = foldOrder(list, reset), folded = none, best = null;
    for (var way = 0; way < 3; way += 1) {
      m = gridMetrics(finest.span, finest.rows, way === 2, way === 1);
      measure(list, m);
      folded = new Set();
      for (var count = 0; count <= order.length; count += 1) {
        found = tryPack(list, m, folded, withCard, false);
        if (found) {
          for (var back = count - 1; back >= 0; back -= 1) {     // one that folded for another's sake may be whole
            folded.delete(order[back]);                          // after all (the newest of them first)
            var again = tryPack(list, m, folded, withCard, false);
            if (again) {
              found = again;
            } else {
              folded.add(order[back]);
            }
          }
          found.folds = folded.size;
          if (!best || found.folds < best.folds) {
            best = found;
          }
          break;
        }
        if (count < order.length) {
          folded.add(order[count]);
        }
      }
    }
    if (best) {
      return best;
    }
    return tryPack(list, m, folded, false, true);     // more than even tiles have room for: what fits, the rest counted
  }

  function keepPack(list, withCard) {          // what is placed stays; what is new, or was opened, takes a free place
    var m = lay.m;
    if (!m || m.w !== root.clientWidth || m.h !== root.clientHeight) {
      return null;
    }
    measure(list, m);
    var occ = blockedCells(m), cells = new Map(), heads = new Map(), todo = [], ok = true;
    if (withCard) {
      var had = lay.cells.get(card.el);
      if (had) {
        cells.set(card.el, had);
        takeCells(occ, had);
      } else {
        todo.push(null);
      }
    }
    list.forEach(function (rec) {
      var was = lay.cells.get(rec.el);
      var full = rec.closed ? !!was && was.kind === 'full' : (!rec.fold || rec.pinned || rec.expanded);
      if (was && (was.kind === 'full') === full) {
        cells.set(rec.el, was);
        takeCells(occ, was);
      } else {
        todo.push({rec: rec, full: full});
      }
    });
    todo.forEach(function (item) {
      var cell = null;
      if (!ok) {
        return;
      }
      if (!item) {
        cell = firstFit(occ, m, m.span, m.sub, 1);
      } else if (item.full) {
        cell = firstFit(occ, m, frameCols(item.rec, m), frameLines(item.rec, m, false), 1);
      } else {
        cell = firstFit(occ, m, m.tile, 1, 1);
      }
      if (!cell) {
        ok = false;
        return;
      }
      cell.kind = !item ? 'card' : (item.full ? 'full' : 'tile');
      cells.set(item ? item.rec.el : card.el, cell);
    });
    lay.heads.forEach(function (el, id) {      // a head stays over its frame while that frame keeps its place
      var cell = cells.get(el);
      if (groups.has(id) && cell && cell.top) {
        heads.set(id, el);
      }
    });
    return ok ? {m: m, cells: cells, heads: heads, over: 0} : null;
  }

  function sameCell(a, b) {
    return !!a && !!b && a.cx === b.cx && a.cy === b.cy && a.cw === b.cw && a.ch === b.ch
      && (a.top || 0) === (b.top || 0);
  }

  function samePack(a, b) {                    // two packs place everything alike
    if (!a || !b || a.m.span !== b.m.span || a.m.rows !== b.m.rows || a.m.tight !== b.m.tight || a.m.tile !== b.m.tile
        || a.cells.size !== b.cells.size || a.heads.size !== b.heads.size) {
      return false;
    }
    var same = true;
    a.cells.forEach(function (cell, el) {
      same = same && sameCell(cell, b.cells.get(el));
    });
    a.heads.forEach(function (el, id) {
      same = same && el === b.heads.get(id);
    });
    return same;
  }

  function schedulePack(mode) {                // '' places what changed; 'full' packs everything again
    if (!canvas) {
      return;
    }
    if (mode === 'full') {
      lay.mode = 'full';
    }
    if (!lay.timer) {
      lay.timer = setTimeout(function () {
        lay.timer = 0;
        packCanvas(lay.mode);
      }, PACK_MS);
    }
  }

  function packCanvas(mode) {
    clearTimeout(lay.timer);
    lay.timer = 0;
    lay.mode = '';
    if (!canvas || !root) {
      return;
    }
    var list = packItems(), withCard = !!card && list.length > 0;
    var found = mode === 'full' ? null : keepPack(list, withCard);
    if (!found) {
      found = canonical(list, withCard, false);
    }
    applyPack(found, list);
    // Something to tidy: a fresh pack would place something otherwise (after a fresh pack: only what the owner opened)
    lay.untidy = list.length > 0 && (mode === 'full'
      ? list.some(function (rec) { return rec.expanded && !rec.pinned; })
      : !samePack(found, canonical(list, withCard, true)));
    lay.packs += 1;
    syncCanvasBoard();
  }

  // ---- a place given: where an element is, the glide to it ---------------------------------------------------------

  function springAt(t) {                       // a spring let go at 0, settled at 1: a little past its end and back
    var damping = 0.62, rate = 11.5, swing = rate * Math.sqrt(1 - damping * damping);
    return 1 - Math.exp(-damping * rate * t) * (Math.cos(swing * t) + (damping * rate / swing) * Math.sin(swing * t));
  }

  function glide(el, dx, dy) {                 // from where it was to its place: a spring (calm: no overshoot)
    if (K === 0 || !el.animate || (Math.abs(dx) < 1 && Math.abs(dy) < 1)) {
      return;
    }
    if (motion !== 'lively') {
      el.animate([{transform: 'translate(' + dx + 'px, ' + dy + 'px)'}, {transform: 'none'}],
                 {duration: GLIDE_MS * 0.7 * K, easing: easeOut});
      return;
    }
    var steps = [];
    for (var step = 0; step <= 18; step += 1) {
      var left = 1 - springAt(step / 18);
      steps.push({transform: 'translate(' + (dx * left).toFixed(1) + 'px, ' + (dy * left).toFixed(1) + 'px)'});
    }
    steps[steps.length - 1] = {transform: 'none'};
    el.animate(steps, {duration: GLIDE_MS * K, easing: 'linear'});
  }

  function cellBox(cell, m) {                  // a place in px (under its group's head tile, when it carries one)
    var top = cell.top || 0;
    return {x: Math.round(cell.cx * m.unit), y: Math.round(cell.cy * (m.subH + m.gap)) + top,
            w: Math.round(cell.cw * m.unit - m.gap), h: Math.round(cell.ch * (m.subH + m.gap) - m.gap) - top};
  }

  function putAt(el, box, fresh) {
    var was = placed.get(el);
    el.style.left = box.x + 'px';
    el.style.top = box.y + 'px';
    el.style.width = box.w + 'px';
    el.style.height = box.h ? box.h + 'px' : '';
    el.classList.remove('unplaced');
    placed.set(el, box);
    if (!was || fresh || K === 0) {
      return;
    }
    glide(el, was.x - box.x, was.y - box.y);
    if (el.classList.contains('task') && el.animate && box.h && was.h && (was.w !== box.w || was.h !== box.h)) {
      el.animate([{width: was.w + 'px', height: was.h + 'px'}, {width: box.w + 'px', height: box.h + 'px'}],
                 {duration: SHRINK_MS * K, easing: easeOut});     // the task card shrinks to its group's head tile
    }
  }

  function ensureFx(rec) {                     // the canvas's own parts of a frame, made once: the scan line, the
    if (rec.fx) {                              // changed light, "MORE" for a body its place cuts
      return;
    }
    rec.fx = true;
    var scan = make('span', 'fx-scanbox');
    scan.setAttribute('aria-hidden', 'true');
    scan.appendChild(make('i', 'fx-scan'));
    var pulse = make('span', 'fx-pulse');
    pulse.setAttribute('aria-hidden', 'true');
    var more = make('span', 'f-cut t-badge', TASK_WORDS.more);
    more.setAttribute('aria-hidden', 'true');
    rec.el.append(scan, pulse, more);
  }

  function cardFreeBox() {                     // nothing else on the canvas: the card beside the orb, as the mock has it
    var box = root.getBoundingClientRect(), places = orbPlaces(), el = card.el;
    var width = Math.round(Math.min(CARD_WIDTH, window.innerWidth * 0.34));
    var orbLeft = places.rest.x - STAGE * places.fit * 0.3;
    var x = Math.max(box.left, Math.min(window.innerWidth * 0.064, orbLeft - 20 - width));
    el.style.width = width + 'px';
    el.style.height = '';
    var height = el.offsetHeight;
    var y = Math.max(box.top, Math.min(window.innerHeight * 0.62, box.bottom - height - 8));
    return {x: Math.round(x - box.left), y: Math.round(y - box.top), w: width, h: 0};
  }

  function applyPack(found, list) {
    var m = found.m;
    lay.over = 0;
    root.classList.toggle('tight', m.tight);
    list.forEach(function (rec) {
      var el = rec.el, cell = found.cells.get(el);
      if (!cell) {
        el.classList.add('over');              // (counted: board.layout().over)
        lay.over += 1;
        return;
      }
      el.classList.remove('over');
      ensureFx(rec);
      var tile = cell.kind === 'tile', held = el.contains(document.activeElement), wasSat = rec.sat;
      rec.fold = tile && !rec.closed;          // (a closed frame's row is no tile: brought back, it is whole)
      rec.sat = rec.fold;
      el.classList.toggle('sat', rec.sat);
      el.classList.toggle('wide', !tile && cell.cw > m.span);
      el.tabIndex = rec.sat || rec.closed ? -1 : 0;
      rec.satButton.tabIndex = rec.sat ? 0 : -1;
      if (!rec.sat) {
        rec.satButton.setAttribute('aria-expanded', rec.expanded ? 'true' : 'false');
      }
      putAt(el, cellBox(cell, m), rec.wantBoot || rec.wantOut);
      if (held && wasSat !== rec.sat && !rec.closed) {
        target(rec).focus({preventScroll: true});     // a frame that folded or opened keeps the keyboard
      }
    });
    groups.forEach(function (group) {
      var under = found.heads.get(group.id), cell = under ? found.cells.get(under) : null;
      if (!cell) {
        if (group.head) {
          group.head.el.classList.add('away');
        }
        return;
      }
      var head = group.head || (group.head = makeHead('head'));
      var fresh = !head.el.isConnected;
      if (fresh) {
        root.appendChild(head.el);
      }
      head.el.classList.remove('away');
      syncHead(group);
      var over = cellBox(cell, m);
      putAt(head.el, {x: over.x, y: over.y - (cell.top || 0), w: over.w, h: HEAD_H}, fresh);
      if (fresh && K > 0 && head.el.animate) {
        head.el.animate([{opacity: 0}, {opacity: 1}], {duration: 220 * K, easing: easeOut});
      }
    });
    if (card) {
      var spot = found.cells.get(card.el);
      var arriving = !placed.get(card.el);
      putAt(card.el, spot ? cellBox(spot, m) : cardFreeBox(), arriving);
      card.el.classList.toggle('free', !spot);
      if (arriving && K > 0 && card.el.animate) {
        card.el.animate([{opacity: 0, transform: 'translateY(10px)'}, {opacity: 1, transform: 'none'}],
                        {duration: 260 * K, easing: easeOut});
      }
    }
    lay.cells = found.cells;
    lay.heads = found.heads;
    lay.m = m;
    list.forEach(function (rec) {              // a body its place cuts says so (Enter opens it big: all of it)
      var body = rec.body;
      rec.el.classList.toggle('cut', !rec.sat && !rec.closed && !!body && body.scrollHeight > body.clientHeight + 1);
    });
    bootPending(list);
    letOut(list);
    scheduleMaths();
    scheduleLayout();
  }

  // ---- the boot of an arriving frame --------------------------------------------------------------------------------

  function bootPending(list) {
    var index = 0;
    var base = BOOT.first + (lay.adopted && performance.now() - lay.adopted < 200 ? SHRINK_MS * 0.5 * K : 0);
    list.forEach(function (rec) {
      if (!rec.wantBoot) {
        return;
      }
      rec.wantBoot = false;
      if (K === 0 || index >= BOOT.max || rec.el.classList.contains('over')) {
        landed(rec, 'done');                   // there at once
        return;
      }
      boot(rec, base + index * BOOT.stagger * K);
      index += 1;
    });
  }

  function boot(rec, delay) {
    var el = rec.el;
    el.style.setProperty('--d', Math.round(delay) + 'ms');
    el.classList.remove('boot');
    void el.getBoundingClientRect();
    el.classList.add('boot');
    rec.booting = true;
    clearTimeout(rec.drawTimer);
    clearTimeout(rec.outlineTimer);
    rec.outlineTimer = setTimeout(function () { landed(rec, 'outline'); }, delay + BOOT.outline * K);
    rec.drawTimer = setTimeout(function () {
      el.classList.remove('boot');
      el.style.removeProperty('--d');
      rec.booting = false;
      landed(rec, 'done');
    }, delay + BOOT.total * K + 50);
    if (motion === 'lively' && !rec.sat) {     // (calm: no decode, no count-up)
      decode(rec, delay + BOOT.content);
      Array.prototype.forEach.call(rec.body.querySelectorAll('.ch-v, .nb-fig'), function (node) {
        countUp(node, delay + BOOT.content + 60);
      });
    }
  }

  function decode(rec, delay) {                // the heading decodes into place: its letters settle left to right
    var heading = rec.heading, text = rec.data ? rec.data.title : '';
    if (!text) {
      return;
    }
    var over = make('span', 'd-fx');           // over the real words (which keep the heading's size and its name)
    over.setAttribute('aria-hidden', 'true');
    heading.classList.add('decoding');
    heading.appendChild(over);
    var began = performance.now() + delay, last = 0;
    (function tick(now) {
      if (!over.isConnected) {
        heading.classList.remove('decoding');  // (the frame was filled again meanwhile)
        return;
      }
      var at = (now - began) / BOOT.decode;
      if (at >= 1) {
        over.remove();
        heading.classList.remove('decoding');
        return;
      }
      if (at >= 0 && now - last > 30) {
        last = now;
        var done = Math.floor(at * text.length), shown = text.slice(0, done);
        for (var index = done; index < Math.min(text.length, done + 4); index += 1) {
          shown += text.charAt(index) === ' ' ? ' '
            : DECODE_GLYPHS.charAt(Math.floor(Math.random() * DECODE_GLYPHS.length));
        }
        over.textContent = shown;
      }
      requestAnimationFrame(tick);
    })(performance.now());
  }

  function countUp(node, delay) {              // a number counts up to its words (which stand again at the end)
    var text = node.textContent, parts = COUNT_TEXT.exec(text);
    if (!parts) {
      return;
    }
    var grouped = parts[2].indexOf(',') >= 0, places = parts[3] ? parts[3].length - 1 : 0;
    var value = parseFloat(parts[2].replace(/,/g, '') + (parts[3] || ''));
    var began = performance.now() + delay, last = 0;
    node.textContent = parts[1] + (0).toFixed(places) + parts[4];
    (function tick(now) {
      if (!node.isConnected) {
        return;
      }
      var at = (now - began) / BOOT.count;
      if (at >= 1) {
        node.textContent = text;
        return;
      }
      if (at >= 0 && now - last > 30) {
        last = now;
        var words = (value * easeOutCubic(at)).toFixed(places);
        if (grouped) {
          var dot = words.indexOf('.'), head = dot < 0 ? words : words.slice(0, dot);
          words = head.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (dot < 0 ? '' : words.slice(dot));
        }
        node.textContent = parts[1] + words + parts[4];
      }
      requestAnimationFrame(tick);
    })(performance.now());
  }

  // ---- a group's head tile and the task card (one element: the card shrinks to the head) ---------------------------

  function clockWords(seconds) {
    var whole = Math.max(0, Math.floor(seconds));
    var minutes = Math.floor(whole / 60), rest = whole % 60;
    return (minutes < 10 ? '0' : '') + minutes + ':' + (rest < 10 ? '0' : '') + rest;
  }

  function makeHead(cls) {
    lay.heads_made += 1;
    var el = make('section', 'task ' + cls);
    var title = make('h2', 't-h');
    title.id = 'task-' + lay.heads_made;
    el.setAttribute('aria-labelledby', title.id);
    var time = make('span', 't-time c-mono');
    var top = make('div', 't-hd');
    top.append(title, time);
    var doing = make('div', 't-words c-mono');
    doing.setAttribute('aria-live', 'polite');
    var blocks = make('div', 't-blocks');
    blocks.setAttribute('aria-hidden', 'true');
    var cells = [];
    for (var index = 0; index < TASK_BLOCKS; index += 1) {
      var block = make('i', '');
      cells.push(block);
      blocks.appendChild(block);
    }
    var counts = make('span', 't-counts c-mono');
    var stop = button('c-btn', '', function () { post('go', {to: 'stop'}); });
    stop.textContent = TASK_WORDS.stop;
    var foot = make('div', 't-ft');
    foot.append(counts, stop);
    el.append(top, doing, blocks, foot);
    return {el: el, title: title, time: time, doing: doing, blocks: cells, counts: counts, stop: stop,
            base: 0, at: 0, timer: 0, sources: 0, took: 0};
  }

  function countWords(count, one, many) {
    return count + (count === 1 ? one : many);
  }

  function syncHead(group) {                   // the head tile's words: the topic, the time, what the answer holds
    var head = group.head;
    if (!head) {
      return;
    }
    var first = null, count = 0;
    Array.prototype.forEach.call(group.list.children, function (node) {
      var rec = records.get(node);
      if (current(rec) && rec.data) {
        first = first || rec;
        count += rec.away || rec.closed ? 0 : 1;
      }
    });
    head.title.textContent = group.topic.textContent || (first ? first.data.title : '');
    head.time.textContent = group.time.textContent;
    var parts = [countWords(count, WORDS.frame, WORDS.frames)];
    if (head.sources) {
      parts.push(countWords(head.sources, TASK_WORDS.source, TASK_WORDS.sources));
    }
    if (head.took) {
      parts.push(clockWords(head.took));
    }
    head.counts.textContent = parts.join(' ' + WORDS.dot + ' ');
  }

  function dropHead(group) {
    if (group && group.head) {
      group.head.el.remove();
      group.head = null;
    }
    if (group) {
      lay.heads.delete(group.id);
    }
  }

  function tickCard() {
    if (card) {
      var words = clockWords(card.base + (performance.now() - card.at) / 1000);
      if (words !== card.time.textContent) {   // (drawn again only when it reads otherwise)
        card.time.textContent = words;
      }
    }
  }

  function leaveCard(gone) {                   // the card goes (stopped, or an answer with nothing for the canvas)
    var el = gone.el;
    clearInterval(gone.timer);
    lay.cells.delete(el);
    if (K === 0 || !el.animate) {
      el.remove();
      return;
    }
    el.classList.add('leaving');
    after(el.animate([{opacity: 1}, {opacity: 0}], {duration: FADE_MS * K, easing: easeOut, fill: 'forwards'}),
          function () { el.remove(); });
  }

  // The task card: what Jarvis is doing while he works, in the app's own words (never the owner's), the time, the
  // counts so far, blocks that fill as steps finish, and Stop (the app's own). "done" with the answer's group: the
  // card becomes that group's head tile - it shrinks to the head's place and its heading is the answer's topic.
  HANDLERS.task = function (message) {
    if (!onlyKeys(message, ['type', 'state', 'title', 'words', 'seconds', 'steps', 'sources', 'frames', 'group', 'stop'])
        || TASK_STATES.indexOf(message.state) < 0 || !words(message.title, TASK_CAPS.title)
        || !words(message.words, TASK_CAPS.words) || !whole(message.seconds, 0, TASK_CAPS.seconds)
        || !whole(message.steps, 0, TASK_CAPS.count) || !whole(message.sources, 0, TASK_CAPS.count)
        || !whole(message.frames, 0, MAX_FRAMES) || typeof message.group !== 'string'
        || (message.group !== '' && !groups.has(message.group)) || typeof message.stop !== 'boolean') {
      return drop('shape');
    }
    if (!canvas) {
      return true;                             // (a board beside a conversation shows no card)
    }
    if (message.state === 'working') {
      var fresh = !card;
      if (fresh) {
        card = makeHead('working unplaced');
        root.appendChild(card.el);
        card.timer = setInterval(tickCard, 250);
      }
      card.title.textContent = message.title;
      card.doing.textContent = message.words;
      card.base = message.seconds;
      card.at = performance.now();
      card.sources = message.sources;
      tickCard();
      var parts = [];
      if (message.steps) {
        parts.push(countWords(message.steps, TASK_WORDS.step, TASK_WORDS.steps));
      }
      if (message.sources) {
        parts.push(countWords(message.sources, TASK_WORDS.source, TASK_WORDS.sources));
      }
      if (message.frames) {
        parts.push(countWords(message.frames, WORDS.frame, WORDS.frames));
      }
      card.counts.textContent = parts.join(' ' + WORDS.dot + ' ');
      var lit = Math.min(TASK_BLOCKS - 1, message.steps * 2);
      card.blocks.forEach(function (block, index) {
        block.className = index < lit ? 'on' : (index === lit ? 'next' : '');
      });
      card.stop.hidden = !message.stop;
      if (fresh) {
        schedulePack('');
      }
      return true;
    }
    if (!card) {
      return true;
    }
    var mine = card;
    card = null;
    clearInterval(mine.timer);
    var group = message.state === 'done' && message.group ? groups.get(message.group) : null;
    if (!group || group.parked) {
      leaveCard(mine);
      schedulePack('');
      return true;
    }
    if (group.head && group.head !== mine) {
      group.head.el.remove();
    }
    lay.cells.delete(mine.el);
    group.head = mine;
    mine.sources = message.sources;
    mine.took = mine.base + (performance.now() - mine.at) / 1000;
    mine.blocks.forEach(function (block) { block.className = 'on'; });
    mine.el.classList.remove('working', 'free', 'unplaced');
    mine.el.classList.add('head');
    lay.adopted = performance.now();           // its frames boot once it has shrunk to their head
    syncHead(group);
    schedulePack('full');
    return true;
  };

  // ---- Tidy, the keyboard, Clear view --------------------------------------------------------------------------------

  function tidy() {                            // every frame back in its place: what the owner opened folds again
    var held = !!canvas && document.activeElement === canvas.tidy;
    frames.forEach(function (rec) {
      if (rec.expanded) {
        rec.expanded = false;
        rec.satButton.setAttribute('aria-expanded', 'false');
      }
    });
    packCanvas('full');
    post('go', {to: 'tidy'});
    if (held) {
      focusBoard();                            // its button is gone with the mess: the keyboard goes to the frames
    }
  }

  function apart(low, high, from, to) {        // how far two spans are from overlapping (0: they overlap)
    return Math.max(0, from - high, low - to);
  }

  function nextOnCanvas(rec, key) {            // the arrows: the nearest frame that way, by where they are on screen
    var all = boardFrames().filter(function (item) { return !item.el.classList.contains('over'); });
    if (key === 'Home' || key === 'End') {
      var ordered = all.map(function (item) { return {rec: item, cell: lay.cells.get(item.el) || {cx: 0, cy: 0}}; })
        .sort(function (a, b) { return a.cell.cy - b.cell.cy || a.cell.cx - b.cell.cx; });     // reading order
      var edge = ordered[key === 'Home' ? 0 : ordered.length - 1];
      return edge ? edge.rec : null;
    }
    var from = rec.el.getBoundingClientRect(), best = null, least = Infinity;
    all.forEach(function (item) {
      if (item === rec) {
        return;
      }
      var to = item.el.getBoundingClientRect(), ahead = 0, off = 0, skew = 0;
      if (key === 'ArrowRight' || key === 'ArrowLeft') {
        ahead = key === 'ArrowRight' ? to.left - from.right : from.left - to.right;
        off = apart(from.top, from.bottom, to.top, to.bottom);
        skew = Math.abs(to.top - from.top);
      } else {
        ahead = key === 'ArrowDown' ? to.top - from.bottom : from.top - to.bottom;
        off = apart(from.left, from.right, to.left, to.right);
        skew = Math.abs(to.left - from.left);
      }
      if (ahead < -1) {
        return;                                // not that way
      }
      var cost = ahead + off * 4 + skew * 0.01;
      if (cost < least) {
        least = cost;
        best = item;
      }
    });
    return best;
  }

  function orbCornerAt() {                     // the orb's place in its corner, in the board's own box
    var box = root.getBoundingClientRect();
    return {x: window.innerWidth - CORNER_INSET - box.left, y: window.innerHeight - CORNER_INSET - box.top};
  }

  function lineFor(rec, box) {
    var line = make('i', 'fx-line' + (rec.el.classList.contains('waits') ? ' amber' : ''));
    line.setAttribute('aria-hidden', 'true');
    line.style.left = box.x + 'px';
    line.style.top = Math.round(box.y + box.h / 2 - 1) + 'px';
    line.style.width = box.w + 'px';
    root.appendChild(line);
    return line;
  }

  function intoOrb(box) {                      // a line's last transform: at the orb's centre, almost a point
    var to = orbCornerAt();
    return 'translate(' + (to.x - (box.x + box.w / 2)).toFixed(1) + 'px, ' + (to.y - (box.y + box.h / 2)).toFixed(1)
      + 'px) scaleX(0.02)';
  }

  function drawIntoOrb(recs) {                 // Clear view: each frame folds to a bright line the orb draws in
    if (!canvas || K === 0 || !recs.length || !root.animate) {
      return;
    }
    var lively = motion === 'lively', longest = 0;
    recs.forEach(function (rec, index) {
      var el = rec.el, box = placed.get(el);
      if (!box || !box.h || el.classList.contains('over') || el.classList.contains('away')) {
        return;
      }
      var delay = (lively ? index * LINE_STAGGER : 0) * K;
      rec.folding = true;
      el.classList.add('folding');
      var fold = el.animate([{transform: 'none', opacity: 1}, {transform: 'scaleY(0.015)', opacity: 1, offset: 0.85},
                             {transform: 'scaleY(0.015)', opacity: 0}],
                            {duration: FOLD_MS * K, delay: delay, easing: 'cubic-bezier(0.5, 0, 0.75, 0.3)',
                             fill: 'forwards'});
      var line = lineFor(rec, box), end = intoOrb(box);
      var fly = line.animate([{transform: 'none', opacity: 0}, {transform: 'none', opacity: 1, offset: 0.18},
                              {transform: end, opacity: 1, offset: 0.94}, {transform: end, opacity: 0}],
                             {duration: LINE_MS * K, delay: delay + FOLD_MS * 0.7 * K,
                              easing: 'cubic-bezier(0.6, 0, 0.9, 0.55)', fill: 'both'});
      longest = Math.max(longest, delay + FOLD_MS * 0.7 * K + LINE_MS * K);
      after(fly, function () {
        line.remove();
        fold.cancel();
        rec.folding = false;
        el.classList.remove('folding');
        el.classList.toggle('away', rec.away);
      });
    });
    lay.hold = performance.now() + longest + 90;      // the orb keeps its corner until the last line is in
    setTimeout(function () {
      if (ORB && ORB.flatTo && lively) {
        ORB.click = performance.now();         // ... and takes them with one small beat
        wake();
      }
    }, Math.max(0, longest - 60));
  }

  function drawOutOfOrb(recs) {                // Bring back: they come out again once they have their places
    if (!canvas || K === 0 || !recs.length) {
      return;
    }
    recs.forEach(function (rec) {
      rec.wantOut = true;
      rec.el.classList.add('unplaced');
    });
  }

  function letOut(list) {                      // the lines out of the orb to their places, then each unfolds
    var out = list.filter(function (rec) { return rec.wantOut; });
    if (!out.length) {
      return;
    }
    var lively = motion === 'lively', now = performance.now();
    var base = ORB && ORB.trip ? Math.max(0, ORB.trip.began + ORB.trip.ms * 0.8 - now) : 0;     // the orb is on its
    out.forEach(function (rec, index) {                                                         // way to the corner
      var el = rec.el, box = placed.get(el);
      rec.wantOut = false;
      if (K === 0 || !box || !box.h || !el.animate || el.classList.contains('over')) {
        return;
      }
      var delay = base + (lively ? index * LINE_STAGGER : 0) * K;
      var line = lineFor(rec, box), start = intoOrb(box);
      after(line.animate([{transform: start, opacity: 0}, {transform: start, opacity: 1, offset: 0.08},
                          {transform: 'none', opacity: 1, offset: 0.9}, {transform: 'none', opacity: 0}],
                         {duration: LINE_MS * K, delay: delay, easing: 'cubic-bezier(0.1, 0.6, 0.3, 1)', fill: 'both'}),
            function () { line.remove(); });
      el.animate([{transform: 'scaleY(0.015)', opacity: 0}, {transform: 'scaleY(0.015)', opacity: 1, offset: 0.05},
                  {transform: lively ? 'scaleY(1.04)' : 'none', opacity: 1, offset: 0.75},
                  {transform: 'none', opacity: 1}],
                 {duration: FOLD_MS * 1.3 * K, delay: delay + LINE_MS * 0.85 * K, easing: easeOut, fill: 'backwards'});
    });
  }

  function setFormSoon(form) {                 // the orb leaves its corner only once Clear view's last line is in
    clearTimeout(lay.formTimer);
    var wait = lay.hold - performance.now();
    if (wait > 0 && ORB.form === 'corner' && form !== 'corner') {
      lay.formTimer = setTimeout(function () { setForm(form); }, wait);
      return;
    }
    setForm(form);
  }

  function layoutLook() {                      // the canvas's layout now, in numbers (the probes' own word on it)
    var m = lay.m, area = root.getBoundingClientRect();
    function box(el) {
      var r = el.getBoundingClientRect();
      return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
    }
    var items = [];
    frames.forEach(function (rec) {
      var cell = lay.cells.get(rec.el);
      items.push({id: rec.id, kind: cell ? cell.kind : '', shown: !!cell && !rec.away && !parked(rec), sat: rec.sat,
                  closed: rec.closed, pinned: rec.pinned, group: rec.group, box: box(rec.el),
                  cell: cell ? [cell.cx, cell.cy, cell.cw, cell.ch] : null,
                  cut: rec.el.classList.contains('cut'), wide: rec.el.classList.contains('wide'),
                  boot: rec.el.classList.contains('boot'), over: rec.el.classList.contains('over')});
    });
    var heads = [];
    groups.forEach(function (group) {
      if (group.head && group.head.el.isConnected && !group.head.el.classList.contains('away')) {
        heads.push({group: group.id, box: box(group.head.el), title: group.head.title.textContent,
                    counts: group.head.counts.textContent, time: group.head.time.textContent});
      }
    });
    var places = orbPlaces();
    var reach = STAGE * CORNER_SCALE * clamp(places.fit, 0.8, 1) * 0.36 + 16;
    return {
      area: [Math.round(area.left), Math.round(area.top), Math.round(area.width), Math.round(area.height)],
      win: [window.innerWidth, window.innerHeight],
      grid: m ? {span: m.span, rows: m.rows, sub: m.sub, tile: m.tile, tight: m.tight, gap: m.gap} : null,
      orb: [Math.round(window.innerWidth - CORNER_INSET - reach), Math.round(window.innerHeight - CORNER_INSET - reach)],
      items: items, heads: heads, untidy: lay.untidy, over: lay.over, packs: lay.packs, filter: filter,
      card: card ? {box: box(card.el), title: card.title.textContent, words: card.doing.textContent,
                    time: card.time.textContent, counts: card.counts.textContent, stop: !card.stop.hidden,
                    blocks: card.blocks.filter(function (block) { return block.className === 'on'; }).length,
                    free: card.el.classList.contains('free')} : null,
      lines: root.querySelectorAll('.fx-line').length
    };
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
    document.addEventListener('animationend', entranceOver);
    document.addEventListener('dragstart', function (event) { event.preventDefault(); });   // nothing leaves the page
    window.addEventListener('resize', function () {
      placeBig();                              // first: the board keeps its own box in a wide view (open big)
      placeCanvas();                           // the canvas: the orb's place and size, the board's top
      schedulePack('full');                    // ... and its frames packed for the new size
      measureWidth();
      scheduleView();
      scheduleLayout();
      scheduleMaths();
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
        return Object.assign({frames: frames.size, groups: groups.size, big: big ? big.state : '', lit: lit,
                              canvas: canvas ? canvas.form : '', orb: canvas ? canvas.orbState : '',
                              task: card ? 'working' : ''}, dropped);
      },
      orb: function () {                       // the orb as it is drawn now, in numbers (null: no canvas)
        return canvas ? orbLook() : null;
      },
      layout: function () {                    // the canvas's layout as it is now, in numbers (null: no canvas)
        return canvas ? layoutLook() : null;
      }
    }),
    writable: false, configurable: false, enumerable: false
  });
  start();
}());
