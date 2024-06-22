import asyncio
import pytz

class MockAppDaemon:
    """Implementation of appdaemon's internal AppDaemon class suitable for testing"""
    def __init__(self, **kwargs):

        #
        # Import various AppDaemon bits and pieces now to avoid circular import
        #

        from appdaemontestframework.appdaemon_mock.futures import MockFutures  # pylint: disable=import-outside-toplevel

        # Use UTC timezone just for testing.
        self.tz = pytz.timezone('UTC')
        self.futures = MockFutures(self)
        self.sched = MockScheduler(self)

