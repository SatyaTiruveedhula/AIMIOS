from .engine import BaseEngine

class ReplayEngine(BaseEngine):
    name = "Replay"

    def start(self) -> None:
        super().start()
        # TODO: start replay capture and playback

    def stop(self) -> None:
        super().stop()
        # TODO: stop replay engine
