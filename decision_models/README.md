# decision_models

把决策模型包放在此目录，然后在界面点击「选择决策模型」导入。

## 体积提示

开源模型的大小差别很大：

| 模型包 | 体积 |
|---|---|
| `vela_v4.vpk` | 约 1 GB（`VELAV4` 查表式模型） |
| `vela_v4.1.vpk` | 约 38 MB（COMPACT48 量化模型） |

两者由不同的原生引擎解释，**可以同时导入**。包头部会声明自己需要哪个引擎；
缺这个字段时按 v4 处理，所以旧包无需改动。

## 第三方来源

`vela_v4` / `vela_v4.1` 模型来自开源项目
[alfm201/adventure](https://github.com/alfm201/adventure)。
详见 `native/vela/README.md` 与项目根 `README.md` 的说明。
