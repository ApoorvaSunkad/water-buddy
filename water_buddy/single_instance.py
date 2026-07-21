"""Guarantee only one copy of the app runs at a time.

Why this is not optional once you ship to other people:

  * Launch-on-startup is enabled by default for most users, so the app is
    already running when they double-click the desktop icon "to open it".
  * Two instances means two schedulers, so reminders arrive in pairs.
  * Worse, both write ``stats.json``. Each does read-modify-write on the glass
    count, so one silently overwrites the other and the number goes backwards.

The mechanism is a named local socket (a named pipe on Windows). On startup we
try to *connect* to it:

  * Connect succeeds -> somebody is already listening, so we are the second
    copy. Send a nudge so the first copy pops its settings window, then exit.
  * Connect fails -> nobody is home, so we become the listener and run.

The nudge matters. Silently exiting would make double-clicking the icon look
broken -- nothing happens, no window, no feedback. Instead the running copy
surfaces its window, which is exactly what the user wanted when they clicked.

A named pipe is preferable to a lock file because Windows destroys it when the
owning process dies, even on a hard kill. A lock file would survive a crash and
lock the user out of their own app until they found and deleted it.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_MS = 300


class SingleInstance(QObject):
    """Detects an existing instance; signals when another copy tries to start.

    Usage::

        guard = SingleInstance("WaterBuddy")
        if guard.already_running:
            sys.exit(0)
        guard.activated.connect(window.show)
    """

    #: A second copy was launched and asked us to come to the front.
    activated = Signal()

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None
        self.already_running = self._try_notify_existing()
        if not self.already_running:
            self._become_server()

    # ------------------------------------------------------------------
    def _try_notify_existing(self) -> bool:
        """Return True if another instance answered our connection attempt."""
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False

        log.info("Another instance is already running; asking it to show")
        socket.write(b"show")
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
        return True

    def _become_server(self) -> None:
        # A previous run that was killed without cleanup can leave a stale name
        # bound. removeServer clears it; it is a no-op when nothing is stale.
        QLocalServer.removeServer(self._key)

        self._server = QLocalServer(self)
        if not self._server.listen(self._key):
            # Not fatal: the app still works, we just lose the guard. Better to
            # run without single-instance protection than to refuse to start.
            log.warning("Could not claim single-instance name: %s",
                        self._server.errorString())
            self._server = None
            return

        self._server.newConnection.connect(self._on_new_connection)
        log.info("Holding single-instance lock")

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        socket.disconnected.connect(socket.deleteLater)
        log.info("A second instance was launched; surfacing the window")
        self.activated.emit()
