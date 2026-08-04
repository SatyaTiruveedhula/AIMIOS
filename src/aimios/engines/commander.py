from .engine import BaseEngine

class CommanderEngine(BaseEngine):
    name = "Commander"

    def start(self) -> None:
        super().start()
        # TODO: initialize commander workflows and user commands

    def stop(self) -> None:
        super().stop()
        # TODO: stop commander engine
