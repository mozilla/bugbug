#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Library with Pulse factory that can generate Pulse consumers.

Import the ConsumerFactory and generate the connection & consumer you need.

```python
    # Righ now the factory only has hg_pushes to generate a connection & consumer
    connection, consumer = ConsumerFactory.hg_pushes(user, password, _on_message)
    with connection as conn:  # noqa
        consumer.run()
```
"""
from kombu import Connection, Exchange, Queue
from kombu.mixins import ConsumerMixin

_CONNECTION_URL = "amqp://{}:{}@pulse.mozilla.org:5671/?ssl=1"


def _generate_hg_pushes_queue(user):
    return Queue(
        name="queue/{}/pushes".format(user),
        exchange=Exchange("exchange/hgpushes/v2", type="topic", no_declare=True,),
        routing_key="#",
        durable=True,
        # XXX: This should not be auto delete
        auto_delete=True,
    )


class _GenericConsumer(ConsumerMixin):
    def __init__(self, connection, queues, callback):
        self.connection = connection
        self.queues = queues
        self.callback = callback

    def get_consumers(self, Consumer, channel):
        return [Consumer(queues=self.queues, callbacks=[self.callback])]


class ConsumerFactory:
    @staticmethod
    def hg_pushes(user, password, callback):
        connection = Connection(_CONNECTION_URL.format(user, password))
        queues = [_generate_hg_pushes_queue(user)]
        consumer = _GenericConsumer(connection, queues, callback)
        return connection, consumer
