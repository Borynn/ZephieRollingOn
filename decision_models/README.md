# decision_models

把决策模型包放在此目录，然后在界面点击「选择决策模型」导入。

仓库及正式 Release 的模型白名单为 `zephie_m1_points.zm`、`vela_v4.2.vpk` 与 `lyra_v1.vpk`。
其他模型可以放在本地使用，但不提交到仓库，也不加入正式 Release。

随仓库及 Release 分发的 `lyra_v1.vpk`（约 25 MB）已内置可读引擎、FP32 权重、特征、导入图片和上游许可。
默认优先 GPU（DirectML），不可用或推理失败时自动回退 CPU；实际设备显示在导入状态和决策建议中。
使用新格式需要更新一次软件；以后同一 API 和共享依赖范围内的新 VPK 可直接放进此目录，
不需要修改软件，已有 VELA 包继续使用。详见 [包内引擎接口](../docs/EMBEDDED_VPK.md)。

## 体积提示

开源模型的大小差别很大：

| 模型包 | 体积 |
|---|---|
| `vela_v4.vpk` | 约 1 GB（`VELAV4` 查表式模型） |
| `vela_v4.1.vpk` | 约 38 MB（COMPACT48 量化模型） |
| `vela_v4.2.vpk` | 约 87 MB（COMPACT100 Hinge 量化模型） |

三者由不同的原生引擎解释，**可以同时导入**。包头部会声明自己需要哪个引擎；
缺这个字段时按 v4 处理，所以旧包无需改动。

`vela_v4.2.vpk` 与 `lyra_v1.vpk` 随仓库及正式 Release 分发；v4、v4.1 等本地可选包不在发布白名单中。

闭源 `.zm` 包未经事先书面许可禁止商用，仅允许按本项目公开调用契约及同款宿主调用方式使用，
须注明项目名称与地址并保留导入展示。完整条款见 [NOTICE 第 3 节](../NOTICE)。

## 第三方来源

`vela_v4` / `vela_v4.1` 模型来自开源项目
[alfm201/adventure](https://github.com/alfm201/adventure)。
详见 `native/vela/README.md` 与项目根 `README.md` 的说明。
