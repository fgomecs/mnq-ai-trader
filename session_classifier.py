from enum import Enum
import os, sys
sys.path.insert(0, os.getenv("BASE_DIR", r"C:\trading\mnq-ai-trader"))

class SessionType(str, Enum):
    TREND   = "TREND"
    RANGE   = "RANGE"
    NEWS    = "NEWS"
    HOLIDAY = "HOLIDAY"
    UNKNOWN = "UNKNOWN"

_current: SessionType = SessionType.UNKNOWN

def set_session_type(t: SessionType) -> None:
    global _current
    _current = t

def get_current_session_type() -> SessionType:
    return _current
