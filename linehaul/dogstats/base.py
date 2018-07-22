# Copyright (c) 2015, Datadog <info@datadoghq.com>
# Copyright (c) 2018, Donald Stufft <donald@stufft.io>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of Datadog nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import logging

from random import random

import trio.socket

from .context import TimedContextManagerDecorator


logger = logging.getLogger(__name__)


class _BaseTrioDogStatsd:

    encoding = "utf-8"

    def __init__(
        self,
        host="localhost",
        port=8125,
        max_buffer_size=50,
        namespace=None,
        constant_tags=None,
        use_ms=False,
        use_default_route=False,
    ):
        self.host = host
        self.port = port
        self.namespace = namespace
        self.constant_tags = constant_tags
        self.use_ms = use_ms

    def configure(self, **kwargs):
        if hasattr(self, "_socket"):
            raise RuntimeError("Cannot configure after metrics have been sent.")

        for key, value in kwargs.items():
            if key not in {
                "host",
                "port",
                "max_buffer_size",
                "namespace",
                "constant_tags",
                "use_ms=False",
                "use_default_route",
            }:
                raise TypeError(f"Invalid keyword argument: {key}")

            setattr(self, key, value)

    def _add_constant_tags(self, tags):
        if self.constant_tags:
            if tags:
                return tags + self.constant_tags
            else:
                return self.constant_tags
        return tags

    def _escape_event_content(self, string):
        return string.replace("\n", "\\n")

    def _escape_service_check_message(self, string):
        return string.replace("\n", "\\n").replace("m:", "m\:")

    async def _get_socket(self):
        if not getattr(self, "_socket", None):
            sock = trio.socket.socket(trio.socket.AF_INET, trio.socket.SOCK_DGRAM)
            await sock.connect((self.host, self.port))
            self._socket = sock

        return self._socket

    async def _close_socket(self):
        if getattr(self, "_socket", None):
            self._socket.close()
            self._socket = None

    async def _send_to_server(self, packet):
        sock = await self._get_socket()
        await sock.send(packet.encode(self.encoding))

    async def _report(self, metric, metric_type, value, tags, sample_rate):
        """
        Create a metric packet and send it.
        More information about the packets' format:
            http://docs.datadoghq.com/guides/dogstatsd/
        """
        if value is None:
            return

        if sample_rate != 1 and random() > sample_rate:
            return

        # Resolve the full tag list
        tags = self._add_constant_tags(tags)

        # Create/format the metric packet
        payload = "%s%s:%s|%s%s%s" % (
            (self.namespace + ".") if self.namespace else "",
            metric,
            value,
            metric_type,
            ("|@" + str(sample_rate)) if sample_rate != 1 else "",
            ("|#" + ",".join(tags)) if tags else "",
        )

        # Send it
        await self._send_to_server(payload)


class TrioDogStatsd(_BaseTrioDogStatsd):

    OK, WARNING, CRITICAL, UNKNOWN = (0, 1, 2, 3)

    async def gauge(self, metric, value, tags=None, sample_rate=1):
        """
        Record the value of a gauge, optionally setting a list of tags and a
        sample rate.
        >>> await statsd.gauge('users.online', 123)
        >>> await statsd.gauge('active.connections', 1001, tags=["protocol:http"])
        """
        await self._report(metric, "g", value, tags, sample_rate)

    async def increment(self, metric, value=1, tags=None, sample_rate=1):
        """
        Increment a counter, optionally setting a value, tags and a sample
        rate.
        >>> await statsd.increment('page.views')
        >>> await statsd.increment('files.transferred', 124)
        """
        await self._report(metric, "c", value, tags, sample_rate)

    async def decrement(self, metric, value=1, tags=None, sample_rate=1):
        """
        Decrement a counter, optionally setting a value, tags and a sample
        rate.
        >>> await statsd.decrement('files.remaining')
        >>> await statsd.decrement('active.connections', 2)
        """
        metric_value = -value if value else value
        await self._report(metric, "c", metric_value, tags, sample_rate)

    async def histogram(self, metric, value, tags=None, sample_rate=1):
        """
        Sample a histogram value, optionally setting tags and a sample rate.
        >>> await statsd.histogram('uploaded.file.size', 1445)
        >>> await statsd.histogram('album.photo.count', 26, tags=["gender:female"])
        """
        await self._report(metric, "h", value, tags, sample_rate)

    async def distribution(self, metric, value, tags=None, sample_rate=1):
        """
        Send a global distribution value, optionally setting tags and a sample rate.
        >>> await statsd.distribution('uploaded.file.size', 1445)
        >>> await statsd.distribution('album.photo.count', 26, tags=["gender:female"])
        This is a beta feature that must be enabled specifically for your organization.
        """
        await self._report(metric, "d", value, tags, sample_rate)

    async def timing(self, metric, value, tags=None, sample_rate=1):
        """
        Record a timing, optionally setting tags and a sample rate.
        >>> await statsd.timing("query.response.time", 1234)
        """
        await self._report(metric, "ms", value, tags, sample_rate)

    def timed(self, metric=None, tags=None, sample_rate=1, use_ms=None):
        """
        A decorator or context manager that will measure the distribution of a
        function's/context's run time. Optionally specify a list of tags or a
        sample rate. If the metric is not defined as a decorator, the module
        name and function name will be used. The metric is required as a context
        manager.
        ::
            @statsd.timed('user.query.time', sample_rate=0.5)
            async def get_user(user_id):
                # Do what you need to ...
                pass
            # Is equivalent to ...
            async with statsd.timed('user.query.time', sample_rate=0.5):
                # Do what you need to ...
                pass
            # Is equivalent to ...
            start = time.time()
            try:
                get_user(user_id)
            finally:
                await statsd.timing('user.query.time', time.time() - start)
        """
        return TimedContextManagerDecorator(self, metric, tags, sample_rate, use_ms)

    async def set(self, metric, value, tags=None, sample_rate=1):
        """
        Sample a set value.
        >>> await statsd.set('visitors.uniques', 999)
        """
        await self._report(metric, "s", value, tags, sample_rate)

    async def event(
        self,
        title,
        text,
        alert_type=None,
        aggregation_key=None,
        source_type_name=None,
        date_happened=None,
        priority=None,
        tags=None,
        hostname=None,
    ):
        """
        Send an event. Attributes are the same as the Event API.
            http://docs.datadoghq.com/api/
        >>> await statsd.event('Man down!', 'This server needs assistance.')
        >>> await statsd.event('The web server restarted', 'The web server is up again', alert_type='success')  # NOQA
        """
        title = self._escape_event_content(title)
        text = self._escape_event_content(text)

        # Append all client level tags to every event
        tags = self._add_constant_tags(tags)

        string = "_e{%d,%d}:%s|%s" % (len(title), len(text), title, text)
        if date_happened:
            string = "%s|d:%d" % (string, date_happened)
        if hostname:
            string = "%s|h:%s" % (string, hostname)
        if aggregation_key:
            string = "%s|k:%s" % (string, aggregation_key)
        if priority:
            string = "%s|p:%s" % (string, priority)
        if source_type_name:
            string = "%s|s:%s" % (string, source_type_name)
        if alert_type:
            string = "%s|t:%s" % (string, alert_type)
        if tags:
            string = "%s|#%s" % (string, ",".join(tags))

        if len(string) > 8 * 1024:
            raise Exception(
                'Event "%s" payload is too big (more than 8KB), '
                "event discarded" % title
            )

        await self._send_to_server(string)

    async def service_check(
        self, check_name, status, tags=None, timestamp=None, hostname=None, message=None
    ):
        """
        Send a service check run.
        >>> await statsd.service_check('my_service.check_name', statsd.WARNING)
        """
        message = (
            self._escape_service_check_message(message) if message is not None else ""
        )

        string = "_sc|{0}|{1}".format(check_name, status)

        # Append all client level tags to every status check
        tags = self._add_constant_tags(tags)

        if timestamp:
            string = "{0}|d:{1}".format(string, timestamp)
        if hostname:
            string = "{0}|h:{1}".format(string, hostname)
        if tags:
            string = "{0}|#{1}".format(string, ",".join(tags))
        if message:
            string = "{0}|m:{1}".format(string, message)

        await self._send_to_server(string)
