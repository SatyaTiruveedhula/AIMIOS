from aimios.market.market_snapshot import MarketSnapshot


def test_market_snapshot_basic():
    snap = MarketSnapshot(0, "TEST", 100.0, 10)
    assert snap.symbol == "TEST"
    assert snap.price == 100.0
