"""Async RabbitMQ consumers (aio-pika).

These replace the Taskiq workers. The outbox relay publishes plain-JSON events to the
``medical.events`` topic exchange; each consumer binds its queue to one topic, runs the
mapped handler, and manages delivery itself (manual ack = at-least-once, bounded retry,
then the existing ``failed_jobs`` dead-letter table). Same async-native concurrency model
as before (one event loop + a prefetch window), just without the Taskiq abstraction.
"""
