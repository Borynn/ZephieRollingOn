# native/vela

开源模型的原生引擎。`*.vpk` 包依赖这里面的扩展才能导入。

## 这里面是什么

| 文件 | 说明 |
|------|------|
| `vela_official.cp3XX-win_amd64.pyd` | VELA **v4** 推理引擎（`VELAV4` 权重，CPU） |
| `vela_official_v41.cp3XX-win_amd64.pyd` | VELA **v4.1** 推理引擎（COMPACT48 权重，**仅 CPU**） |

## 为什么是两个引擎

v4 与 v4.1 的**权重格式互斥**，且 pybind 模块名决定了一个进程只能导入一个同名模块，
所以它们是两个独立模块：

| | v4 | v4.1 |
|---|---|---|
| 模块名 | `vela_official` | `vela_official_v41` |
| 权重格式 | `VELAV4`，**恰好** 1,096,193,164 字节 | COMPACT48（`2SCV`），39,512,548 字节 |
| 权重体积 | 约 1 GB | **37.7 MB** |
| 搜索 | depth=2，window=24 | depth=2，horizon=48 |
| GPU | 可选 HIP | 无（`device_ready()` 恒为 False） |

两者**可以同时加载**（内存各占一份），界面按包内 `engine_module` 字段选择用哪个。
v4 的包没有这个字段，回落到 `vela_official`。

> 注意：先加载 v4 再加载 v4.1 会同时占用约 1.06 GB；反之亦然。这是模型自身的
> 体积，不是重复拷贝。

**这不是本项目的代码**，而是第三方开源项目
[alfm201/adventure](https://github.com/alfm201/adventure) 中
`src/policies/vela/native` 的编译结果，仅做了一层薄封装（状态转换 + 返回值映射）。
版权与许可归上游作者；如与上游许可冲突，以上游为准。

⚠ v4.1 的引擎源码在**上游 `main` 分支**，不在 `vela-v4.1` 标签里——那个标签仍带着 v4
的 `native` 目录（`inference.hpp` 是 7962 B 的旧版，读不了 COMPACT48 权重）。
取源码时请用 `main`：

```text
https://raw.githubusercontent.com/alfm201/adventure/main/src/policies/vela/native/<file>
```

需要的文件：`core.hpp`、`compact.hpp`、`inference.hpp`、`bridge.cpp`、`tables.hpp`、
`deck-coefficients.hpp`。其中 `compact.hpp` 与 `core.hpp` 是 v4.1 新增的。

## 为什么提交二进制而不是只放源码

`.pyd` 分发给终端用户时是**必要运行时依赖**，不是可再生的构建中间产物——
用户机器上没有 MSVC / pybind11，也不该要求他们编译。上游模型本身是开源的，
所以这里提交的是「上游源码的一份可执行编译」，不含任何私有逻辑。

## 构建（需要时自行重编）

需要一个装了 `pybind11` 的 Python 环境 + Visual Studio Build Tools。
上游的源码与 `setup.py` 在此仓库中未镜像，请从上游项目获取（v4.1 用上面的 `main` 路径）
后自行编写 `setup.py`：把 `pybind_module.cpp` 编成 `vela_official_v41` 扩展，
源文件只需 `pybind_module.cpp`（它 `#include "bridge.cpp"`）。

要点：
- **Python 版本必须与运行环境一致**。本项目便携版用 **Python 3.11**，
  所以文件名是 `vela_official_v41.cp311-win_amd64.pyd`。用 3.12 编出来的
  `cp312` 在 3.11 里无法 import。
- v4 默认是 **CPU** 后端，可选 HIP 加速见上游 README（需要 ROCm 与
  `HIP_VISIBLE_DEVICES=1`）。v4.1 只有 CPU。
- 本仓库不镜像上游源码，避免与上游许可产生冲突。

## 加载路径

`zephie_rolling_on.decision_models.vela_package` 按以下顺序查找扩展：

1. 已安装的同名模块
2. 项目根下 `native/vela/`（本目录）
3. 可执行文件同级目录（便携包解压后的布局）

查找用的是 `<模块名>.*.pyd` 这种**带点**的模式：不带点的 `vela_official*` 会同时
匹配到 `vela_official_v41`，从而加载错误的引擎。

找不到时 `import_model` 会返回明确错误，不会静默失败。
