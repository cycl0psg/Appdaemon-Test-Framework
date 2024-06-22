from appdaemontestframework.appdaemon_mock.appdaemon import MockAppDaemon


class MockFutures:
    def __init__(self, ad: MockAppDaemon):
        self.AD = ad
        self.futures = {}

    def add_future(self, name, f):
        if name not in self.futures:
            self.futures[name] = []

        self.futures[name].append(f)

    def remove_future(self, name, f):
        if name in self.futures:
            self.futures[name].remove(f)

        if f.cancelled():
            return

        if not f.done():
            f.cancel()

    def cancel_futures(self, name):
        if name not in self.futures:
            return

        for f in self.futures[name]:
            if not f.done() and not f.cancelled():
                f.cancel()
