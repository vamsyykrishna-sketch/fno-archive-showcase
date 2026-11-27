from pathlib import Path

from fyers_apiv3 import fyersModel

from fyers_settings import (
    FYERS_CLIENT_ID,
    FYERS_ACCESS_TOKEN,
)

# fyersModel writes fyersApi.log into log_path at import time. An empty
# string resolves to the current working directory, which under cron isn't
# guaranteed to be writable (or even /opt/fno-archive) -- pin it to
# this file's own directory instead so it works regardless of caller cwd.
LOG_DIR = str(Path(__file__).resolve().parent) + "/"

fyers = fyersModel.FyersModel(
    client_id=FYERS_CLIENT_ID,
    token=FYERS_ACCESS_TOKEN,
    is_async=False,
    log_path=LOG_DIR
)
