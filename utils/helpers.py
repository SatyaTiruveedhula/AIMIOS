from typing import Any, Dict


def ensure_dict(obj: Any) -> Dict:
    return obj if isinstance(obj, dict) else {}
