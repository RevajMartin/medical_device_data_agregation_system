"""RabbitMQ topology shared by the outbox relay (producer) and the consumers.

The relay publishes each committed outbox event to a single durable **topic** exchange
with the event's ``topic`` as the routing key; queues are bound by topic, so each
consumer receives exactly its events. Both the relay and the consumers declare the
exchange + queues + bindings (declarations are idempotent), so an event is never
published to an unrouted exchange even if a consumer has not started yet — the durable
queue holds it until the consumer comes up.

Adding a new background job is therefore one line here (queue -> topic) plus a consumer
entry in ``src/consumers/__main__.py`` and an ``emit_outbox_event(db, "<topic>", {...})``
call from the producer.
"""

EVENTS_EXCHANGE = "medical.events"

# queue name -> binding key (the outbox event topic that queue consumes)
QUEUE_BINDINGS: dict[str, str] = {
    "alerts": "measurement.created",
    "scoring": "riskscore.requested",
}

# Topics the relay knows how to route. An unknown topic is left unacked (not lost),
# so it can be handled once a queue/binding is added for it.
KNOWN_TOPICS = frozenset(QUEUE_BINDINGS.values())
