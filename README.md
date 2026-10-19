# Zephie Rolling On

游戏辅助脚本：窗口绑定、画面识别、自动点击，以及决策模型调用。

Package: `zephie_rolling_on` · Dist: `zephie-rolling-on`

版本号单一来源为 `src/zephie_rolling_on/__init__.py` 的 `__version__`；运行时的版本可在界面的「导出日志」里看到。

**系统要求：Windows 10 / Windows 11，仅 64 位。**

**交流 QQ 群：`582530986`** — 使用问题、更新通知、反馈都在这儿。

**许可：CC BY-NC-SA 4.0。** 详见 [LICENSE](LICENSE)；第三方内容与免责说明见 [NOTICE](NOTICE)。

需要注意以下事项：

- `decision_models/zephie_*.zm` 是本项目自研的**闭源模型包**，适用独立使用条款：
  **未经权利人事先书面许可，禁止商用**；只允许按本项目公开的模型调用契约与同款
  宿主调用方式使用未修改的包，使用时必须注明本项目名称及地址
  **<https://github.com/Borynn/ZephieRollingOn>**。须保留导入流程中的 Zephie 展示与署名，
  不得修改、反编译或提取权重。完整条款见 [NOTICE 第 3 节](NOTICE#3-自研闭源模型包zm)。
- LYRA 的模型数据与上游衍生引擎适用包内各自许可，不适用 `.zm` 的闭源使用条款。
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

仓库与正式 Release 分发 `m1_points`、`VELA v4.2` 和 `LYRA v1`。其他模型可在本地加载，
不提交到仓库，也不加入正式 Release。

两种包格式按扩展名分派，界面统一展示：

| 扩展名 | 加载方式 |
|--------|----------|
| `zephie_*.zm` | 交给同目录的 `model_host.exe`（独立进程） |
| `*.vpk` | 旧 VELA 包使用原生引擎；新格式可内置可读引擎与模型资源 |

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

## 第三方 LYRA 模型与包内引擎

`lyra_v1.vpk` 随仓库及正式 Release 分发，来源为
[alfm201/adventure](https://github.com/alfm201/adventure)，作者为 `alfm201`，
冻结上游提交 `f173abf7f502e5969917f81a1bd02745d7e834f8`。
包内包含 LYRA V20 FP32 / pi20 一步评估的可读引擎、ONNX 权重、特征、导入图片、来源及许可。
本项目做了状态/动作映射、上游模型导出、包内引擎适配与设备回退，未重新训练模型。

- 默认优先 DirectML GPU，初始化或推理失败后自动切换 CPU；实际设备显示在导入状态及建议中。
- 支持兼容 DirectML 的 AMD、Intel、NVIDIA 显卡，不需要另装 CUDA。显卡/驱动不兼容时走 CPU；
  多显卡机器默认使用适配器 0，不保证选择最快的显卡。
- 本机 Ryzen 7 7800X3D / Radeon RX 7900 XT 的 333 局面对照：决策中位数约 GPU **28 ms**、
  CPU **45 ms**；这是本机参考，不是所有设备的耗时保证。上游研究参考终格约 **1879 格**，
  不代表本项目重新完成了同规模对局评测。
- 引擎与模型资源内置在 VPK，软件提供共享 NumPy/ONNX Runtime。首次需要支持包内引擎的新版软件；
  后续兼容 API v1 和现有依赖的新 VPK 可独立更新，旧 VELA 包继续可用。
- LYRA 模型数据依上游授权可用于包括商业用途在内的使用与分发，衍生引擎保留上游 0BSD 许可，
  详见包内 `LICENSES.txt`、`SOURCE.json` 及 [NOTICE](NOTICE)。这些授权不改变本软件、`.zm`
  或界面图片的各自条款。

接口及打包方式见 [包内引擎文档](docs/EMBEDDED_VPK.md)。只导入可信来源的 VPK：包内引擎会执行本地代码。

## 功能

- 控制面板、热键、卡池校准
- 截屏识别、自动点击、补骰


