from functools import wraps
from time import time


class TimedContextManagerDecorator(object):
    """
    A context manager and a decorator which will report the elapsed time in
    the context OR in a function call.
    """

    def __init__(self, statsd, metric=None, tags=None, sample_rate=1, use_ms=None):
        self.statsd = statsd
        self.metric = metric
        self.tags = tags
        self.sample_rate = sample_rate
        self.use_ms = use_ms
        self.elapsed = None

    def __call__(self, func):
        """
        Decorator which returns the elapsed time of the function call.

        Default to the function name if metric was not provided.
        """
        if not self.metric:
            self.metric = "%s.%s" % (func.__module__, func.__name__)

        @wraps(func)
        async def wrapped(*args, **kwargs):
            start = time()
            try:
                return await func(*args, **kwargs)
            finally:
                await self._send(start)

        return wrapped

    async def __aenter__(self):
        if not self.metric:
            raise TypeError("Cannot used timed without a metric!")
        self.start = time()
        return self

    async def __aexit__(self, type, value, traceback):
        # Report the elapsed time of the context manager.
        await self._send(self.start)

    async def _send(self, start):
        elapsed = time() - start
        use_ms = self.use_ms if self.use_ms is not None else self.statsd.use_ms
        elapsed = int(round(1000 * elapsed)) if use_ms else elapsed
        await self.statsd.timing(self.metric, elapsed, self.tags, self.sample_rate)
        self.elapsed = elapsed

    async def start(self):
        await self.__enter__()

    async def stop(self):
        await self.__exit__(None, None, None)
