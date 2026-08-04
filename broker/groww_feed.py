"""Groww broker feed implementation."""


class GrowwFeed:
    """Groww API feed connector."""

    def connect(self):
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def get_quote(self, instrument):
        raise NotImplementedError
