"""Constants for the Doremi DCP-2000 integration."""

DOMAIN = "doremi_dcp2000"
MANUFACTURER = "Doremi Labs"

DEFAULT_PORT = 5000
DEFAULT_KLV_PORT = 11730
DEFAULT_SCAN_INTERVAL = 2  # seconds
DEFAULT_FPS = 24.0
LIBRARY_REFRESH = 300  # seconds

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
