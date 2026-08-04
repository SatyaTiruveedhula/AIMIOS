"""Market state tracking utilities."""


class MarketState:
    """Represents the current state of the market."""

    def __init__(self, session_status, indices, breadth):
        self.session_status = session_status
        self.indices = indices
        self.breadth = breadth
