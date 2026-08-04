from .engine import BaseEngine

class CoolingEngine(BaseEngine):
    name = "Cooling"

    def start(self) -> None:
        super().start()
        # TODO: start cooling/heatmap logic

    def stop(self) -> None:
        super().stop()
        # TODO: stop cooling engine
