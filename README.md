# Zephie Rolling On

游戏辅助脚本：窗口绑定、画面识别、自动点击，以及决策模型调用。

软件版本可在界面的「导出日志」中查看。

**系统要求：Windows 10 / Windows 11，仅 64 位。**

**交流 QQ 群：`582530986`** — 使用问题、更新通知、反馈都在这儿。

**许可：CC BY-NC-SA 4.0。** 详见 [LICENSE](LICENSE)；第三方内容与免责说明见 [NOTICE](NOTICE)。

模型使用许可：

- `decision_models/zephie_*.zm` 是本项目自研的**闭源模型包**，适用独立使用条款：
  **未经权利人事先书面许可，禁止商用**；只允许按本项目公开的模型调用契约与同款
  宿主调用方式使用未修改的包，使用时必须注明本项目名称及地址
  **<https://github.com/Borynn/ZephieRollingOn>**。须保留导入流程中的 Zephie 展示与署名，
  不得修改、反编译或提取权重。完整条款见 [NOTICE](NOTICE)。
- VELA / LYRA 系列模型及配套推理组件由上游作者统一授权，可随本项目按
  CC BY-NC-SA 4.0 分发，不适用 `.zm` 的闭源使用条款；版权仍归上游作者，详见 [NOTICE](NOTICE)。

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

仓库与正式 Release 内置 `m1_points`、`VELA v4.2` 和 `LYRA v1`。
其他兼容模型也可放入此目录使用。

两种包格式按扩展名分派，界面统一展示：

| 扩展名 | 加载方式 |
|--------|----------|
| `zephie_*.zm` | 交给同目录的 `model_host.exe`（独立进程） |
| `*.vpk` | 旧 VELA 包使用原生引擎；新格式可内置可读引擎与模型资源 |

使用 `.zm` 模型时，请保留软件随附的 `model_host.exe`，并保持模型包原文件名。

与当前软件接口兼容的新模型包可独立更新：下载后放入 `decision_models/`，重新选择并导入即可。

## 第三方 VELA / LYRA 系列模型

VELA 与 LYRA 均由 `alfm201` 开发，来自同一项目
[alfm201/adventure](https://github.com/alfm201/adventure)。
作者 `alfm201` 已书面允许本项目使用、修改、打包和再分发这些组件，并允许本项目按
CC BY-NC-SA 4.0 发布；授权记录见
[Issue #5](https://github.com/alfm201/adventure/issues/5#issuecomment-6138391205)。
版权仍归 `alfm201` 所有，该授权不覆盖其无权授权的第三方游戏图片或其他素材。

软件内置 VELA v4.2 和后续模型 LYRA v1，并兼容已有的 VELA v4.1 模型包：

| 包 | 权重 | 推理方式 |
|---|---|---|
| `vela_v4.1.vpk` | COMPACT48，约 38 MB | CPU，搜索窗口 48 |
| `vela_v4.2.vpk` | COMPACT100 Hinge，约 87 MB | CPU，搜索窗口 100 |
| `lyra_v1.vpk` | FP32，约 25 MB | GPU 优先，失败回退 CPU |

- 默认优先 DirectML GPU，初始化或推理失败后自动切换 CPU；实际设备显示在导入状态及建议中。
- 支持兼容 DirectML 的 AMD、Intel、NVIDIA 显卡，不需要另装 CUDA。
- 参考决策耗时：GPU **28 ms**、CPU **45 ms**（Ryzen 7 7800X3D / Radeon RX 7900 XT，
  决策中位数）；实际耗时随设备与局面变化。上游研究参考终格约 **1879 格**。
- 首次使用 LYRA 请更新到支持包内引擎的新版软件；原有 VELA 模型仍可继续使用。

只导入可信来源的 VPK：包内引擎会执行本地代码。
开发与打包说明见 [包内引擎文档](docs/EMBEDDED_VPK.md)。

## 功能

- 控制面板、热键、卡池校准
- 截屏识别、自动点击、补骰


