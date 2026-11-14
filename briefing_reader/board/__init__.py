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

Nothing in the app makes a board yet (part 5 puts it on Home). Importing this package imports nothing of the web
engine; only ``view`` starts it, and only through snapshot_engine.
"""
