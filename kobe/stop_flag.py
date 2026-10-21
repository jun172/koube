# stop_flag.py

_ai_stop_flag = False
_x_stop_flag = False

def request_stop_ai():
    global _ai_stop_flag
    _ai_stop_flag = True

def reset_stop_ai():
    global _ai_stop_flag
    _ai_stop_flag = False

def is_ai_stop_requested() -> bool:
    global _ai_stop_flag
    return _ai_stop_flag

def request_stop_x():
    global _x_stop_flag
    _x_stop_flag = True

def reset_stop_x():
    global _x_stop_flag
    _x_stop_flag = False

def is_x_stop_requested() -> bool:
    global _x_stop_flag
    return _x_stop_flag