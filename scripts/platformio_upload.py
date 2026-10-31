Import("env")

import os
import sys

project_dir = env.subst("$PROJECT_DIR")
bridge_dir = os.path.join(project_dir, ".nrf-dfu-bridge")

upload_script = os.path.join(
    bridge_dir,
    "scripts",
    "nrf_dfu_upload.py"
)

config_file = os.path.join(
    bridge_dir,
    "boards",
    "xiao_nrf52840.json"
)

command = '"{}" "{}" "$SOURCE" "{}"'.format(
    sys.executable,
    upload_script,
    config_file
)

env.Replace(UPLOADCMD=command)

print("NRF DFU Bridge upload configured")
