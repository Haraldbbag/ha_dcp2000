"""Constants for the Doremi DCP-2000 integration."""

DOMAIN = "doremi_dcp2000"
MANUFACTURER = "Doremi Labs"

DEFAULT_PORT = 5000
DEFAULT_KLV_PORT = 11730
DEFAULT_SCAN_INTERVAL = 2  # seconds
DEFAULT_FPS = 24.0
LIBRARY_REFRESH = 300  # seconds
CPL_INFO_BATCH = 25  # GetCPLInfo requests per update for CPLs without clip id

CONF_SCAN_INTERVAL = "scan_interval"
CONF_FPS = "fps"
CONF_GPIO = "gpio"
CONF_KLV = "klv"
CONF_KLV_PORT = "klv_port"
CONF_KLV_REG = "klv_reg"

GPIO_LINES = 8
START_TIMEOUT = 90  # seconds the server may spend on security checks after Play
BURST_SECONDS = 8  # fast polling after a transport command ...
BURST_INTERVAL = 0.5  # ... every half second
PREVIOUS_THRESHOLD = 3  # s into a clip: "previous" restarts it, below that -> previous clip

MODE_CINEMA = "cinema"  # jumps only while paused - like the DCP-2000 itself
MODE_MEDIAPLAYER = "mediaplayer"  # jumps also while playing (pause, jump, play)
TRANSPORT_MODES = [MODE_CINEMA, MODE_MEDIAPLAYER]

# --- Show schedule (SOAP web services, read only) ---
CONF_SCHEDULE = "schedule"
CONF_SOAP_PORT = "soap_port"
CONF_SOAP_USER = "soap_username"
CONF_SOAP_PASSWORD = "soap_password"
CONF_SCHEDULE_INTERVAL = "schedule_interval"  # minutes
CONF_SCHEDULE_DAYS = "schedule_days"
CONF_HISTORY_DAYS = "history_days"
CONF_DAY_START = "day_start_hour"
CONF_PAUSE_LIMIT = "pause_limit"  # minutes
CONF_EXTERNAL_PLAYERS = "external_players"

DEFAULT_SOAP_PORT = 10000
DEFAULT_SOAP_USER = "manager"
DEFAULT_SOAP_PASSWORD = "password"
DEFAULT_SCHEDULE_INTERVAL = 5
DEFAULT_SCHEDULE_DAYS = 14
DEFAULT_HISTORY_DAYS = 183
DEFAULT_DAY_START = 4
DEFAULT_PAUSE_LIMIT = 180
SCHEDULE_FAIL_TOLERANCE = 3  # failed polls before the schedule entities go unavailable
SCHEDULE_INFO_BATCH = 30  # GetScheduleInfo requests per poll (new shows first)
SPL_TITLE_REFRESH = 300  # s between GetSPLListInfo calls for unknown SPLs
