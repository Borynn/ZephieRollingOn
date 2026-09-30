# Zephie Rolling On

游戏辅助脚本：窗口绑定、画面识别、自动点击，以及决策模型调用。

Package: `zephie_rolling_on` · Dist: `zephie-rolling-on` · Version `1.0.1`

**系统要求：Windows 10 / Windows 11，仅 64 位。**

**交流 QQ 群：`582530986`** — 使用问题、更新通知、反馈都在这儿。

**许可：MIT。** 详见 [LICENSE](LICENSE)；第三方内容与免责说明见 [NOTICE](NOTICE)。

需要注意两处例外：

- `decision_models/zephie_*.zm` 是本项目自研的**闭源模型包**，不在 MIT 范围内
  （允许用来决策，不允许修改或提取权重）。
- `native/vela/*.pyd` 与 `*.vpk` 是第三方开源内容，版权归上游作者，按其自身许可发布。

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

## 第三方开源模型：VELA

`*.vpk` 包内的 VELA 来自第三方开源项目，**不是本项目自研模型**：

- 上游项目：**[alfm201/adventure](https://github.com/alfm201/adventure)**
- 使用的部分：`src/policies/vela`（价值表推理，depth=2）
- 本仓库的改动：仅做**薄封装**——把我们的状态转成其快照格式、把返回值转成我们的动作约定；
  推理逻辑与权重均取自上游，未作修改
- 两个版本，各需自己的原生引擎（模块名不同，可同时加载）：

| 包 | 权重 | 搜索 | 来源 |
|---|---|---|---|
| `vela_v4.vpk` | `VELAV4`，约 1 GB | window=24 | 上游 `vela-v4` 标签 |
| `vela_v4.1.vpk` | COMPACT48，约 38 MB | horizon=48 | 上游 `main`（v4.1 标签里仍是 v4 引擎） |

版权与许可归上游作者所有；如上游许可与本项目不同，以**上游许可**为准。

## 功能

- 控制面板、热键、卡池校准
- 截屏识别、自动点击、补骰


