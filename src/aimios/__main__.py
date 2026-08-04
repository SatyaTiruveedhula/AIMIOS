import logging

from .core import AIMIOS
from .engines.pattern_recognition import PatternRecognitionEngine
from .engines.swing_detection import SwingDetectionEngine
from .engines.pressure import PressureEngine
from .engines.cooling import CoolingEngine
from .engines.commander import CommanderEngine
from .engines.replay import ReplayEngine
from .engines.alerts import AlertsEngine
from .ui.app import AIMIOSApp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

def main() -> None:
    app = AIMIOS()
    app.register_engine(PatternRecognitionEngine)
    app.register_engine(SwingDetectionEngine)
    app.register_engine(PressureEngine)
    app.register_engine(CoolingEngine)
    app.register_engine(CommanderEngine)
    app.register_engine(ReplayEngine)
    app.register_engine(AlertsEngine)

    ui = AIMIOSApp(app)
    ui.initialize_engine_list()
    ui.mainloop()
