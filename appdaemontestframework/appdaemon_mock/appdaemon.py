import asyncio
import threading
from types import SimpleNamespace

import pytz


class MockAppDaemon:
    """Implementation of appdaemon's internal AppDaemon class suitable for testing.

    AppDaemon >=4.5 runs the public (sync-looking) ADAPI methods through
    ``appdaemon.utils.sync_decorator``, which pushes the underlying coroutine onto the
    running AppDaemon event loop via ``run_coroutine_threadsafe``. Monkey-patching that
    decorator is not reliable because ``appdaemon.adapi`` is already imported (and its
    methods already decorated) by the time this package is loaded.

    Instead we give the mock a real event loop running in a background thread. The
    decorator's own logic then does the right thing:

    * a call from the test's main thread runs the coroutine on the background loop and
      blocks for the result (``run_coroutine_threadsafe``);
    * a nested call from within a coroutine (e.g. ``run_in`` awaiting ``get_now``) sees
      it is on the loop thread and just schedules a task to ``await``.
    """

    def __init__(self, **kwargs):  # pylint: disable=unused-argument

        #
        # Import various AppDaemon bits and pieces now to avoid circular import
        #

        from appdaemontestframework.appdaemon_mock.futures import MockFutures  # pylint: disable=import-outside-toplevel
        from appdaemontestframework.appdaemon_mock.scheduler import MockScheduler  # pylint: disable=import-outside-toplevel

        # Use UTC timezone just for testing.
        self.tz = pytz.timezone("UTC")
        self.futures = MockFutures(self)

        # Background event loop used to drive ADAPI coroutines.
        self.loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="MockAppDaemonLoop", daemon=True
        )
        self._loop_thread.start()
        self._loop_ready.wait()
        # `sync_decorator` compares this against the current thread id to decide whether
        # it is "in the main thread" (i.e. on the loop). The loop lives in its own thread.
        self.main_thread_id = self._loop_thread.ident

        self.http = None
        self.config = SimpleNamespace(internal_function_timeout=60)

        self.sched = MockScheduler(self)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._loop_ready.set)
        self.loop.run_forever()

    def stop(self):
        """Shut the background loop down. Call from test teardown."""
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._loop_thread.join(timeout=5)
        if not self.loop.is_closed():
            self.loop.close()
