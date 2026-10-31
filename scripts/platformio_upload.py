Import("env")

import os
import sys

# This script lives in <project>/.nrf-dfu-bridge/scripts/.
bridge_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
upload_script = os.path.join(bridge_dir, "scripts", "nrf_dfu_upload.py")
board_config = os.path.join(bridge_dir, "boards", "xiao_nrf52840.json")

command = '"{}" "{}" "$SOURCE" "{}"'.format(
    sys.executable,
    upload_script,
    board_config,
)

env.Replace(UPLOADCMD=command)
print("NRF DFU Bridge upload aktiviert")
