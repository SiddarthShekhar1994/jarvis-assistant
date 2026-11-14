"""A stand-in for the board's web view in the shared test process (the new look, part 5a): the web engine never starts
in a unit test (tests/board_probe.py runs the real one in processes of its own), so the app's board here is a real
board.view.BoardView whose page is a plain native widget.

    StandInBoard(parent, ready=True)
        everything BoardView does in Python (the checks of every message, the known frames and groups, the queue
        before ready, the signals, the fallback's read-only tiles, the window's keys, Space) - only its page is
        StandInPage: ``sent`` keeps every message the page would have run, and the page paints the bar and each
        frame as a box in board order, so a grab of the window shows the board where it is. ``ready=False``: it
        never says ready (the queue holds); ``say_ready()`` then.
    stand_in_factory(boards)      a board_factory for ui.AppController / AppHarness: each StandInBoard made goes in
                                  ``boards``
    fallback_factory(boards)      the board that can't start here: BoardView with an engine that says no (its
                                  native tile and read-only tiles: the real fallback, no engine)
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from briefing_reader import holo, snapshots
from briefing_reader.board import view

FRAME_H = 96          # a frame's box on the stand-in page
GAP = 12


class StandInPage(QWidget):
    """The stand-in's page: the bar, then each frame as a TILE box with its title, in the page's board order."""

    def __init__(self, board: StandInBoard) -> None:
        super().__init__(board)
        self._board = board
        self.setObjectName("standInPage")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def boxes(self) -> list[tuple[str, QRectF]]:
        """(frame id, its box) in board order."""
        order = self._board.board_order()
        top = 44.0 if self._board.has_bar() else 12.0
        boxes = []
        for frame_id in order:
            boxes.append((frame_id, QRectF(view.PAD, top, max(1.0, self.width() - view.PAD - view.STRIP), FRAME_H)))
            top += FRAME_H + GAP
        return boxes

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._board.has_bar():
                painter.setFont(holo.font("button"))
                painter.setPen(QColor(holo.INK_1))
                painter.drawText(QRectF(view.PAD, 0, 200, 32), int(Qt.AlignmentFlag.AlignVCenter), "Board")
            frames = self._board.frames_sent()
            for frame_id, box in self._board_boxes():
                frame = frames.get(frame_id, {})
                waits = bool(frame.get("waits"))
                painter.setPen(QPen(QColor(holo.AMBER) if waits else holo.FRAME_EDGE, 1.0))
                painter.setBrush(QColor(holo.TILE_AMBER if waits else holo.TILE))
                painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
                painter.setFont(holo.font("frame_title"))
                painter.setPen(QColor(holo.INK_1))
                painter.drawText(box.adjusted(14, 11, -14, -14), int(Qt.AlignmentFlag.AlignLeft), str(frame.get("title", "")))
        finally:
            painter.end()

    def _board_boxes(self) -> list[tuple[str, QRectF]]:
        return self.boxes()


class StandInBoard(view.BoardView):
    """See the module doc."""

    def __init__(self, parent: QWidget | None = None, *, ready: bool = True) -> None:
        super().__init__(parent, start_delay_ms=0, engine=lambda: "", scheme_ready=lambda: True)
        self.sent: list[dict[str, Any]] = []
        self._say_ready = ready
        self.page_widget: StandInPage | None = None
        self.begins = 0                    # how many times it really started (start() is idempotent)

    def _begin(self) -> None:  # the engine never starts here: the stand-in page instead
        if self._phase != view._WAITING:  # noqa: SLF001
            return
        self.begins += 1
        page = StandInPage(self)
        self._layout.addWidget(page)
        self._view = page
        self.page_widget = page
        self._phase = view._LOADING  # noqa: SLF001
        if self._say_ready:
            self._on_ready()

    def say_ready(self) -> None:
        self._on_ready()

    def _run(self, text: str) -> None:
        self.sent.append(json.loads(text))
        if self.page_widget is not None:
            self.page_widget.update()

    # ---- what the page would have now ------------------------------------------------------------------------------

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [message for message in self.sent if message.get("type") == kind]

    def has_bar(self) -> bool:
        return any(message.get("type") == "bar" for message in self.sent)

    def frames_sent(self) -> dict[str, dict[str, Any]]:
        """Each frame id's last frame message (minus those removed with their group)."""
        frames: dict[str, dict[str, Any]] = {}
        gone_groups: set[str] = set()
        for message in self.sent:
            kind = message.get("type")
            if kind == "frame":
                frames[message["id"]] = message
            elif kind == "remove_group":
                gone_groups.add(message["id"])
            elif kind == "clear":
                frames.clear()
        return {key: value for key, value in frames.items() if value.get("group") not in gone_groups}

    def groups_sent(self) -> dict[str, dict[str, Any]]:
        """Each group's last group message, the newest last (minus those removed)."""
        groups: dict[str, dict[str, Any]] = {}
        for message in self.sent:
            kind = message.get("type")
            if kind == "group":
                groups[message["id"]] = message if message["id"] not in groups else {**groups[message["id"]],
                                                                                      **message}
            elif kind == "remove_group":
                groups.pop(message["id"], None)
        return groups

    def board_order(self) -> list[str]:
        """The frame ids as the page would show them: the newest group first, each in its order."""
        frames = self.frames_sent()
        order: list[str] = []
        for group_id, group in reversed(list(self.groups_sent().items())):
            members = [frame_id for frame_id in group.get("frames", []) if frames.get(frame_id, {}).get("group")
                       == group_id]
            members += [frame_id for frame_id, frame in frames.items()
                        if frame.get("group") == group_id and frame_id not in members]
            order += members
        order += [frame_id for frame_id, frame in frames.items() if not frame.get("group")]
        return order


def stand_in_factory(boards: list[Any], **options: Any) -> Any:
    def make(parent: QWidget) -> StandInBoard:
        board = StandInBoard(parent, **options)
        boards.append(board)
        return board

    return make


def fallback_factory(boards: list[Any]) -> Any:
    def make(parent: QWidget) -> view.BoardView:
        board = view.BoardView(parent, start_delay_ms=0, engine=lambda: snapshots.R_NO_ENGINE,
                               scheme_ready=lambda: True)
        boards.append(board)
        return board

    return make
