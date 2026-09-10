"""Lightweight stand-ins for AppDaemon's internal objects (``AppDaemon``, ``Scheduler``).

AppDaemon 4 is built on asyncio: the public ADAPI methods (``run_in``, ``get_now``, ...)
are coroutines exposed to sync app code through ``appdaemon.utils.sync_decorator``. That
decorator schedules the coroutine on the running AppDaemon event loop.

Earlier versions of this framework monkey-patched the decorator, but by the time this
package is imported ``appdaemon.adapi`` is already loaded and its methods already
decorated, so patching has no effect. Instead, :class:`MockAppDaemon` runs a real event
loop in a background thread and lets AppDaemon's own decorator drive coroutines onto it.
"""
