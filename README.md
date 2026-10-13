# Zephie Rolling On

游戏辅助脚本：窗口绑定、画面识别、自动点击，以及决策模型调用。

Package: `zephie_rolling_on` · Dist: `zephie-rolling-on`

版本号单一来源为 `src/zephie_rolling_on/__init__.py` 的 `__version__`；运行时的版本可在界面的「导出日志」里看到。

**系统要求：Windows 10 / Windows 11，仅 64 位。**

**交流 QQ 群：`582530986`** — 使用问题、更新通知、反馈都在这儿。

**许可：CC BY-NC-SA 4.0。** 详见 [LICENSE](LICENSE)；第三方内容与免责说明见 [NOTICE](NOTICE)。

需要注意以下事项：

- `decision_models/zephie_*.zm` 是本项目自研的**闭源模型包**（允许用来决策，不允许修改或提取权重）。
- VELA v4.1/v4.2 模型及所需运行时组件由上游作者书面授权，可随本项目按
  CC BY-NC-SA 4.0 分发；版权仍归上游作者，详见 [NOTICE](NOTICE)。

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

发布新模型包时，用户只需下载对应的模型包文件放进 `decision_models/`，
无需重新下载整个软件。

## 第三方 VELA 模型与运行时

本项目使用的 VELA v4.1/v4.2 模型及其运行时组件来自第三方项目，**不是本项目自研模型**。
作者 `alfm201` 已书面允许本项目使用、修改、打包和再分发这些组件，并允许本项目按
CC BY-NC-SA 4.0 发布；授权记录见
[Issue #5](https://github.com/alfm201/adventure/issues/5#issuecomment-6138391205)。
版权仍归 `alfm201` 所有，该授权不覆盖其无权授权的第三方游戏图片或其他素材。

- 上游项目：**[alfm201/adventure](https://github.com/alfm201/adventure)**
- 使用的部分：`src/zephie_rolling_on/decision_models/vela_package.py`（价值表推理，depth=2）
- 本仓库的改动：仅做**薄封装**——把我们的状态转成其快照格式、把返回值转成我们的动作约定；
  推理逻辑与权重均取自上游，未作修改
- 两个版本，各需自己的原生引擎（模块名不同，可同时加载）：

| 包 | 权重 | 搜索 | 来源 |
|---|---|---|---|
| `vela_v4.1.vpk` | COMPACT48，约 38 MB | horizon=48 | 上游 `main`（v4.1 标签里仍是 v4 引擎） |
| `vela_v4.2.vpk` | COMPACT100 Hinge，约 87 MB | horizon=100 | 上游 `main`@`bc0e2fc6` |

作者不要求署名，但本项目保留上游项目与授权记录以便核查。

## 功能

- 控制面板、热键、卡池校准
- 截屏识别、自动点击、补骰


