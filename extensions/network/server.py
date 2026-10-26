"""Local Network Access — a second uvicorn listener that lives inside the app process.

``uvicorn.Server.serve()`` installs its own SIGINT/SIGTERM handlers when it
runs on the main thread (``capture_signals``), which would take Ctrl-C and the
desktop shell's shutdown signal away from the main server. :class:`LanServer`
turns that off; the listener is stopped by :meth:`LanServer.stop` instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import socket
from collections.abc import Generator

import uvicorn
from starlette.types import ASGIApp

from core.extensions.contract import ServiceStartError

logger = logging.getLogger(__name__)


def bind_failure_message(port: int, exc: OSError) -> str:
    """Return the operator-safe sentence for a failed bind to ``port``."""
    if exc.errno == errno.EADDRINUSE:
        return f"Port {port} is already in use."
    if exc.errno == errno.EACCES:
        return f"BossMod isn’t allowed to use port {port}."
    return f"Couldn’t use port {port}: {exc.strerror or exc}."


def bind_socket(host: str, port: int) -> socket.socket:
    """Bind and return a listening-ready TCP socket.

    Args:
        host: The address to bind (``0.0.0.0`` for every interface).
        port: The port.

    Returns:
        The bound, not yet listening, socket. The caller owns it.

    Raises:
        ServiceStartError: The bind failed (the message names the OS reason).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Same as asyncio's own default: a port in TIME_WAIT is reusable, one
        # with a live listener is not.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        raise ServiceStartError(bind_failure_message(port, exc)) from exc
    return sock


class LanServer(uvicorn.Server):
    """A uvicorn server that serves ``app`` on ``host:port`` and leaves the process's signals alone.

    Args:
        app: The ASGI application to serve (already wrapped by the gate).
        host: The address to bind.
        port: The port; ``0`` picks a free one (see :attr:`bound_port`).
        stop_timeout_seconds: How long open connections get to finish when
            the server stops, before they are cancelled.
    """

    def __init__(self, app: ASGIApp, *, host: str, port: int, stop_timeout_seconds: int) -> None:
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            # The app's own lifespan runs once, under the main server.
            lifespan="off",
            # Leave the process's logging setup alone; this server's own
            # start/stop lines go through the uvicorn loggers.
            log_config=None,
            access_log=False,
            # The client address feeds the login throttle: never take it
            # from a header the client controls.
            proxy_headers=False,
            timeout_graceful_shutdown=stop_timeout_seconds,
        )
        super().__init__(config)
        self._task: asyncio.Task[None] | None = None
        self._socket: socket.socket | None = None

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        """Do not install signal handlers (see the module docstring)."""
        yield

    @property
    def bound_port(self) -> int:
        """The port the socket is bound to; raises ``RuntimeError`` before :meth:`start`."""
        if self._socket is None:
            raise RuntimeError("the server has not been started")
        return self._socket.getsockname()[1]

    async def start(self) -> None:
        """Bind the port and serve in the background; return once it accepts connections.

        Raises:
            ServiceStartError: The port could not be bound, or the server
                stopped before it was ready.
        """
        sock = bind_socket(self.config.host, self.config.port)
        self._socket = sock
        task = asyncio.create_task(self.serve(sockets=[sock]), name="network-access-listener")
        task.add_done_callback(self._log_exit)
        self._task = task
        try:
            while not self.started:
                if task.done():
                    # A crash while starting is not an expected failure: let
                    # its traceback through. A quiet exit still has to say
                    # something.
                    task.result()
                    raise ServiceStartError("The network listener stopped as soon as it started.")
                await asyncio.sleep(0.01)
        except BaseException:
            self._task = None
            self._socket = None
            sock.close()
            if not task.done():
                task.cancel()
            raise

    async def stop(self) -> None:
        """Stop serving and close the socket. Idempotent.

        Raises:
            Exception: Whatever crashed the server task earlier, if it had.
        """
        task, self._task = self._task, None
        if task is None:
            return
        self.should_exit = True
        try:
            await task
        finally:
            if self._socket is not None:
                self._socket.close()
                self._socket = None

    def _log_exit(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("The network listener crashed", exc_info=error)
