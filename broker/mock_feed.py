"""Mock broker feed for local testing."""


class MockFeed:
    """Mock feed connector for testing."""

    def connect(self):
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def get_quote(self, instrument):
        raise NotImplementedError
