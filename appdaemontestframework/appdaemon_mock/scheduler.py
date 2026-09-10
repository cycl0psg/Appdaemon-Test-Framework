import datetime
import functools
import uuid
from typing import List, Optional

import pytz
import time_machine

from appdaemontestframework.appdaemon_mock.appdaemon import MockAppDaemon


class MockScheduler:
    """Implement the AppDaemon Scheduler appropriate for testing and provide extra interfaces for adjusting the simulation"""

    def __init__(self, AD: MockAppDaemon):
        self.AD = AD
        self._registered_callbacks: List["CallbackInfo"] = []
        self._traveller: Optional["time_machine.travel"] = None

        # Default to Jan 1st, 2000 12:00AM
        # internal time is stored as a naive datetime in UTC
        self.sim_set_start_time(datetime.datetime(2000, 1, 1, 0, 0))

    # Implement the AppDaemon APIs for Scheduler
    async def get_now(self) -> datetime.datetime:
        """Return current localized naive datetime"""
        return self.get_now_sync()

    def get_now_sync(self) -> datetime.datetime:
        """Same as `get_now` but synchronous"""
        return pytz.utc.localize(self._now)

    async def get_now_ts(self) -> float:
        """Return the current localized timestamp"""
        return (await self.get_now()).timestamp()

    async def get_now_naive(self) -> datetime.datetime:
        return self.make_naive(await self.get_now())

    async def parse_datetime(self, input_, aware=False, today=None, days_offset=0, now=None):
        """Parse the inputs AppDaemon's scheduler accepts into a datetime.

        Supports the forms the apps actually use: a `datetime`, a `time`, and
        `"HH:MM"` / `"HH:MM:SS"` strings. Sunrise/sunset keywords are not simulated.
        """
        reference = now or (self.get_now_sync() if aware else self._now)
        reference = reference + datetime.timedelta(days=days_offset)

        if isinstance(input_, datetime.datetime):
            parsed = input_
        elif isinstance(input_, datetime.time):
            parsed = datetime.datetime.combine(reference.date(), input_)
        else:
            text = str(input_).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
                try:
                    parsed = datetime.datetime.strptime(text, fmt)
                except ValueError:
                    continue
                if fmt.startswith("%H"):
                    parsed = datetime.datetime.combine(reference.date(), parsed.time())
                break
            else:
                raise ValueError(f"{self.__class__.__name__} cannot parse datetime {input_!r}")

        if aware:
            return self.convert_naive(parsed)
        return self.make_naive(parsed) if parsed.tzinfo is not None else parsed

    async def parse_time(self, time_str, aware=False, today=None, days_offset=0):
        parsed = await self.parse_datetime(time_str, aware=aware, today=today, days_offset=days_offset)
        return parsed.time()

    async def insert_schedule(
        self,
        name,
        aware_dt,
        callback,
        repeat=False,
        type_=None,
        interval=0,
        offset=None,
        random_start=None,
        random_end=None,
        pin=None,
        pin_thread=None,
        **kwargs,
    ):
        # AppDaemon >=4.5 passes `interval` and the scheduler-level params
        # (`random_*`, `pin*`, `offset`) explicitly - they are absorbed by the named
        # arguments above and ignored. Any remaining `**kwargs` are the app's own
        # callback kwargs (this is how older callers, and our own tests, pass them;
        # AppDaemon 4.5's `run_in` instead binds them into the `functools.partial`).
        naive_dt = self.make_naive(aware_dt)
        interval = interval or kwargs.pop("interval", 0)
        callback_kwargs = dict(kwargs)
        callback_kwargs["interval"] = interval
        return self._queue_calllback(callback, callback_kwargs, naive_dt)

    async def timer_running(self, name, handle):
        for callback in self._registered_callbacks:
            if callback.handle == handle:
                return True
        return False

    async def cancel_timer(self, name: str, handle, silent: bool = False) -> None:
        for callback in list(self._registered_callbacks):
            if callback.handle == handle:
                self._registered_callbacks.remove(callback)

    def convert_naive(self, dt):
        # Is it naive?
        result = None
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            # Localize with the configured timezone
            result = self.AD.tz.localize(dt)
        else:
            result = dt

        return result

    def make_naive(self, dt):
        local = dt.astimezone(self.AD.tz)
        return datetime.datetime(
            local.year, local.month, local.day, local.hour, local.minute, local.second, local.microsecond,
        )

    # Test framework simulation functions
    def sim_set_start_time(self, time):
        """Set the absolute start time and set current time to that as well.
        if time is a datetime, it goes right to that.
        if time is time, it will set to that time with the current date.
        All dates/datetimes should be localized naive

        To guarantee consistency, you can not set the start time while any callbacks are scheduled.
        """
        if len(self._registered_callbacks) > 0:
            raise RuntimeError("You can not set start time while callbacks are scheduled")

        if isinstance(time, datetime.time):
            time = datetime.datetime.combine(self._now.date(), time)
        self._start_time = self._now = time
        self._sim_travel_to(time)

    def sim_get_start_time(self):
        """returns localized naive datetime of the start of the simulation"""
        return pytz.utc.localize(self._start_time)

    def sim_elapsed_seconds(self):
        """Returns number of seconds elapsed since the start of the simulation"""
        return (self._now - self._start_time).total_seconds()

    def sim_fast_forward(self, time):
        """Fastforward time and invoke callbacks. time can be a timedelta, time, or datetime (all should be localized naive)"""
        if isinstance(time, datetime.timedelta):
            target_datetime = self._now + time
        elif isinstance(time, datetime.time):
            if time > self._now.time():
                target_datetime = datetime.datetime.combine(self._now.date(), time)
            else:
                # handle wrap around to next day if time is in the past already
                target_date = self._now.date() + datetime.timedelta(days=1)
                target_datetime = datetime.datetime.combine(target_date, time)
        elif isinstance(time, datetime.datetime):
            target_datetime = time
        else:
            raise ValueError(f"Unknown time type '{type(time)}' for fast_forward")

        self._run_callbacks_and_advance_time(target_datetime)

    def sim_stop(self):
        """Stop simulating time. Releases the frozen clock (`time_machine`)."""
        if self._traveller is not None:
            self._traveller.stop()
            self._traveller = None

    # Internal functions
    def _sim_travel_to(self, naive_utc_dt):
        """Freeze the wall clock at the simulated time, replacing any previous freeze.

        This lets code-under-test that reads the real clock directly
        (`datetime.datetime.now()`, `time.time()`, ...) instead of `self.get_now()`
        still observe the simulated time.
        """
        self.sim_stop()
        self._traveller = time_machine.travel(pytz.utc.localize(naive_utc_dt), tick=False)
        self._traveller.start()

    def _queue_calllback(self, callback_function, kwargs, run_date_time):
        """queue a new callback and return its handle"""
        interval = kwargs.get("interval", 0)
        new_callback = CallbackInfo(callback_function, kwargs, run_date_time, interval)

        if new_callback.run_date_time < self._now:
            raise ValueError("Can not schedule events in the past")

        self._registered_callbacks.append(new_callback)
        return new_callback.handle

    def _run_callbacks_and_advance_time(self, target_datetime, run_callbacks=True):
        """run all callbacks scheduled between now and target_datetime"""
        if target_datetime < self._now:
            raise ValueError("You can not fast forward to a time in the past.")

        while True:
            callbacks_to_run = [x for x in self._registered_callbacks if x.run_date_time <= target_datetime]
            if not callbacks_to_run:
                break
            # sort so we call them in the order from oldest to newest
            callbacks_to_run.sort(key=lambda cb: cb.run_date_time)
            # dispatch the oldest callback
            callback = callbacks_to_run[0]
            self._now = callback.run_date_time
            self._sim_travel_to(self._now)
            if run_callbacks:
                callback()
            if callback.interval > 0:
                callback.run_date_time += datetime.timedelta(seconds=callback.interval)
            else:
                if callback in self._registered_callbacks:
                    self._registered_callbacks.remove(callback)

        self._now = target_datetime
        self._sim_travel_to(self._now)

    def __getattr__(self, name: str):
        raise RuntimeError(f"'{name}' has not been mocked in {self.__class__.__name__}")


class CallbackInfo:
    """Class to hold info about a scheduled callback"""

    def __init__(self, callback_function, kwargs, run_date_time, interval):
        self.handle = str(uuid.uuid4())
        self.run_date_time = run_date_time
        self.callback_function = callback_function
        self.kwargs = kwargs
        self.interval = interval

    def __call__(self):
        callback_function = self.callback_function
        # AppDaemon scheduler kwargs (`interval`, `random_start`, ...) are internal and
        # are not passed to the app callback.
        call_kwargs = {k: v for k, v in self.kwargs.items() if k != "interval"}

        # AppDaemon >=4.5 hands the scheduler a `functools.partial` with the user's
        # positional/keyword args pre-bound (see `ADAPI.run_in`). Unwrap it the way
        # `appdaemon.threads.dispatch_worker` does: merge the partial's keywords into the
        # single kwargs dict and call `func(*pos_args, kwargs_dict)`.
        if isinstance(callback_function, functools.partial):
            pos_args = callback_function.args
            call_kwargs.update(callback_function.keywords)
            callback_function.func(*pos_args, call_kwargs)
        else:
            callback_function(call_kwargs)
