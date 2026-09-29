# Zephie Rolling On

游戏辅助脚本：窗口绑定、画面识别、自动点击，以及决策模型调用。

Package: `zephie_rolling_on` · Dist: `zephie-rolling-on`

**系统要求：Windows 10 / Windows 11，仅 64 位。**

**许可：禁止商用。** 详见 [LICENSE](LICENSE)。

## 环境自检

独立的环境检查工具，不随界面启动自动运行：

```bash
python -m zephie_rolling_on.diagnose          # 图形报告窗口
python -m zephie_rolling_on.diagnose --text   # 仅输出到控制台
```

便携包中双击 **`自检.bat`** 即可运行。报告同时保存为 `check_report.txt`。
检查项包括系统版本与位数、管理员权限、DPI、依赖库、配置与图像资源、
截屏后端与实测截屏、模型包与原生引擎。

## 环境（开发）

```bash
conda env create -f environment.yml
# 或
powershell -ExecutionPolicy Bypass -File scripts\setup_conda_env.ps1

conda activate zephie_rolling_on
python -m zephie_rolling_on.ui
```

## 决策模型

模型放在 `decision_models/`，界面点「选择决策模型」导入。接口见
[docs/MODEL_PACKAGE_CONTRACT.md](docs/MODEL_PACKAGE_CONTRACT.md)。

两种包格式按扩展名分派，界面统一展示：

| 扩展名 | 加载方式 |
|--------|----------|
| `zephie_*.zm` | 交给同目录的 `model_host.exe`（独立进程） |
| `*.vpk` | 进程内原生引擎（见 `native/vela/`） |

`model_host.exe` 是本仓库随附的运行时组件（编译产物，非源码）。`.zm` 包是
不透明的数据文件：只定义了包内**是否可被解释**与**是否被改动过**，不含可读的
模型结构或权重。允许用包做决策；**改动过的包会被拒绝加载**。

只发布自研模型包时，用户只需下载对应的 `.zm` 文件放进 `decision_models/`，
无需重新下载整个软件。

## 第三方开源模型：VELA v4

`*.vpk` 包内的 `vela_v4` 来自第三方开源项目，**不是本项目自研模型**：

- 上游项目：**[alfm201/adventure](https://github.com/alfm201/adventure)**
- 使用的部分：`src/policies/vela`（VELA v4 价值表推理，`evaluate` / depth=2 / window=24）
- 本仓库的改动：仅做**薄封装**——把我们的状态转成其快照格式、把返回值转成我们的动作约定；
  推理逻辑与权重均取自上游，未作修改
- 权重文件：因体积约 1 GB，**不纳入本仓库**，请从上游项目或其发布页获取

版权与许可归上游作者所有；如上游许可与本项目不同，以**上游许可**为准。

## 功能

- 控制面板、热键、卡池校准
- 截屏识别、自动点击、补骰


