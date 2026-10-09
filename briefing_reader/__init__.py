"""Jarvis Assistant: a personal AI desktop assistant for Windows.

The package keeps its first name, briefing_reader, so existing setups keep working: the
command (py -3.13 -m briefing_reader), APP_NAME (the %LOCALAPPDATA% data folder, the log file,
the lock and AppUserModelID names) and the scheduled task names stay. DISPLAY_NAME is the name
people see.
"""

__version__ = "1.2.0"
APP_NAME = "briefing-reader"
DISPLAY_NAME = "Jarvis Assistant"
