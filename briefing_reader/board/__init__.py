"""The board (the new look): typed frames of what Jarvis shows, on one locked-down web view beside the conversation.

Built so far (part 3, the host):

* ``scheme``: the board's own address scheme, jarvis-board:, registered before the QApplication on every start; its
  handler serves a fixed allowlist (the page, its style and script, tokens.css written from holo, Qt's own
  qwebchannel.js, the three typefaces) and nothing else.
* ``protocol``: the messages both ways (Qt-free): what the app may send the page (typed, size-capped), and the short
  list of requests the page may make back, each about a frame the app sent.
* ``view``: BoardView, the one QWebEngineView on the page (started through snapshot_engine, so the web engine's safety
  flags always come first), its bridge, its native fallback tile, and the window's keys with the focus in the page.
* ``assets``: board.html (its content security policy), board.css and board.js: Jarvis's own code only, no library;
  every element made with createElement, every word set as text.

Part 4, the frames from what Jarvis already knows:

* ``frames``: the builders (Qt-free) from LIVE's pictures, a web research answer, to-dos and deadlines, and follow-ups
  to the frame messages ``protocol`` checks (calendar, mail_thread, mail_list, todos, picture, and text with rows):
  every string cleaned with live's cleaner and capped as the page counts it.
* ``glance``: the glance painter (QPainter only): read-only tiles of those frames, for the board's fallback (GlanceTiles
  under its "can't start" tile) and later the second screen (private: mail text hidden).

Part 5a, the board on Home:

* ``feed``: what the board holds (Qt-free): one group per answer (its time and topic), the frames in each group in board
  order (what waits on the owner first, then the order Jarvis speaks of them), a mail thread and the reply that waits on
  it as one frame, the limits (24 frames, 8 groups: the oldest group with no pinned frame leaves whole), the bar's
  counts; the messages that put it all on the page.
* ``protocol`` gains the group, remove_group, bar, desk and reveal messages and a frame's group; the page draws the bar
  ("Board", "9 FRAMES", "1 WAITS", All | Pinned and Clear drawn) and the groups' headers, and plays the desk's entrance.

Part 5b, the board's ways:

* ``feed`` is the source of truth for pins (6 at most), close and Bring back (the frame leaves 8 s later), Clear and its
  Bring back (out of sight 8 s, then gone), and parking (an answer that comes while the owner reads waits under "New
  answer above · Show" and lands after 8 quiet seconds; ``ReadingWatch`` is the reading rule); ``facts`` makes a
  satellite's one fact from a frame's data.
* ``protocol`` gains a frame's fact, a group's parked, the cleared and focus_part messages, and the clear, restore,
  show_new, reading and focus_out requests; the page draws the index strip, "N more below", the fades, pairs,
  satellites, the pill, the cleared row, and takes the keyboard (F6 reaches it through ``view``'s key filter).

Part 6, Ask's typed frames:

* ``ask_frames`` (Qt-free): an Ask's or a web research's optional "topic" and "frames" checked strictly (a table, a
  chart, a timeline, maths: types, caps, finite numbers, the allowed maths ops, depth and nodes), every string cleaned
  with live's cleaner; a frame that fails is dropped with a reason word, never the answer. A frame's message, its key
  (its kind and title), a number's words and an expression's plain words.
* ``protocol`` gains the four kinds' bodies; the page renders each with createElement and textContent only (maths from
  its checked tree); ``glance`` paints them (private: a table's headline, maths' answer); ``facts``, ``lit`` and
  ``feed`` know them.

desk_ui.DeskController makes the one board (only while ``[look] board`` is on) and feeds it; desk_view and
ui.ReadingView put it on Home beside the conversation. Importing this package imports nothing of the web engine; only
``view`` starts it, and only through snapshot_engine.
"""
