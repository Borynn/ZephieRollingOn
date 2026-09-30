"""python -m zephie_rolling_on.ui [--check]

``--check`` 运行独立环境自检（见 ``zephie_rolling_on.diagnose``）而不是启动界面。
面向开发者/命令行排查；普通用户请用界面的「导出日志」，它会跑同一套自检并把
报告打进 zip。
"""

import sys

if "--check" in sys.argv:
    from zephie_rolling_on.diagnose import main as _diagnose_main

    raise SystemExit(_diagnose_main(sys.argv[1:]))

from zephie_rolling_on.ui.control_panel import run_app

run_app()
