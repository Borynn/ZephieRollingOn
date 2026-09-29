"""python -m zephie_rolling_on.ui [--check]

``--check`` 运行独立环境自检（见 ``zephie_rolling_on.diagnose``）而不是启动界面。
便携包里的 自检.bat 用这个入口。
"""

import sys

if "--check" in sys.argv:
    from zephie_rolling_on.diagnose import main as _diagnose_main

    raise SystemExit(_diagnose_main(sys.argv[1:]))

from zephie_rolling_on.ui.control_panel import run_app

run_app()
