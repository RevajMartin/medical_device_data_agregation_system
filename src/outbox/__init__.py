# Outbox relay package.
# Note: intentionally no eager imports here. Importing the runnable module
# (`run_outbox_relay`) at package import time would trigger a RuntimeWarning when
# the relay is started via `python -m src.outbox.relay`.
