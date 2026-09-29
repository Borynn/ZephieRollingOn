# native/vela

开源模型的原生引擎。`*.vpk` 包（如 `vela_v4.vpk`）依赖这里面的扩展才能导入。

## 这里面是什么

| 文件 | 说明 |
|------|------|
| `vela_official.cp3XX-win_amd64.pyd` | VELA v4 推理引擎的编译产物（CPU） |

**这不是本项目的代码**，而是第三方开源项目
[alfm201/adventure](https://github.com/alfm201/adventure) 中
`src/policies/vela/native` 的编译结果，仅做了一层薄封装（状态转换 + 返回值映射）。
版权与许可归上游作者；如与上游许可冲突，以上游为准。

## 为什么提交二进制而不是只放源码

`.pyd` 分发给终端用户时是**必要运行时依赖**，不是可再生的构建中间产物——
用户机器上没有 MSVC / pybind11，也不该要求他们编译。上游模型本身是开源的，
所以这里提交的是「上游源码的一份可执行编译」，不含任何私有逻辑。

## 构建（需要时自行重编）

需要一个装了 `pybind11` 的 Python 环境 + Visual Studio Build Tools。
上游的源码与 `setup.py` 在此仓库中未镜像，请从上游项目获取后构建。

要点：
- **Python 版本必须与运行环境一致**。本项目便携版用 **Python 3.11**，
  所以文件名是 `vela_official.cp311-win_amd64.pyd`。用 3.12 编出来的
  `cp312` 在 3.11 里无法 import。
- 默认是 **CPU** 后端，不需要显卡、不需要 ROCm。
  可选 HIP 加速见上游 README（需要 ROCm 与 `HIP_VISIBLE_DEVICES=1`）。
- 本仓库不镜像上游源码，避免与上游许可产生冲突。

## 加载路径

`zephie_rolling_on.decision_models.vela_package` 按以下顺序查找扩展：

1. 已安装的 `vela_official` 模块
2. 项目根下 `native/vela/`（本目录）
3. 可执行文件同级目录（便携包解压后的布局）

找不到时 `import_model` 会返回明确错误，不会静默失败。
