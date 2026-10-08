# native/vela

开源模型的原生引擎。`*.vpk` 包依赖这里面的扩展才能导入。

## 这里面是什么

| 文件 | 说明 |
|------|------|
| `vela_official.cp3XX-win_amd64.pyd` | VELA **v4** 推理引擎（`VELAV4` 权重，CPU） |
| `vela_official_v41.cp3XX-win_amd64.pyd` | VELA **v4.1** 推理引擎（COMPACT48 权重，**仅 CPU**） |
| `vela_official_v42.cp3XX-win_amd64.pyd` | VELA **v4.2** 推理引擎（COMPACT100 Hinge 权重，**仅 CPU**） |

## 为什么是三个引擎

v4 与 v4.1/v4.2 的**权重格式互斥**，且 pybind 模块名决定了一个进程只能导入一个同名
模块，所以它们是三个独立模块：

| | v4 | v4.1 | v4.2 |
|---|---|---|---|
| 模块名 | `vela_official` | `vela_official_v41` | `vela_official_v42` |
| 权重格式 | `VELAV4`，**恰好** 1,096,193,164 字节 | COMPACT48（`2SCV`），39,512,548 字节 | COMPACT100 Hinge（`2SCV`），**恰好** 91,179,876 字节 |
| 权重体积 | 约 1 GB | **37.7 MB** | **87 MB** |
| 搜索 | depth=2，window=24 | depth=2，horizon=48 | depth=2，horizon=100 |
| 策略 | 上游默认 | COMPACT48 | `base`、γ=0、`OPT=4228`（= 上游出厂配置） |
| GPU | 可选 HIP | 无（`device_ready()` 恒为 False） | 无（同 v4.1） |

三者**可以同时加载**（内存各占一份），界面按包内 `engine_module` 字段选择用哪个。
v4 的包没有这个字段，回落到 `vela_official`。

> 注意：先加载 v4 再加载 v4.1 会同时占用约 1.06 GB；v4.2 更重（模型 2.3× v4.1）。
> 这是模型自身的体积，不是重复拷贝。

**这不是本项目的代码**，而是第三方开源项目
[alfm201/adventure](https://github.com/alfm201/adventure) 中
`src/policies/vela/native` 的编译结果，仅做了一层薄封装（状态转换 + 返回值映射）。
版权与许可归上游作者；如与上游许可冲突，以上游为准。

⚠ **v4.1 的引擎源码要按提交号取，不能取 `main`** —— 上游在 2026-10-04（提交
`928fdb25`「VELA v4.2 모델 적용…」）把 **v4.2** 落地到 `main` 时**删掉了
`compact.hpp` 与 `inference.hpp`**，换成 5 个新文件（`compact_factor.hpp`、
`engine_factor.hpp`、`exact_dp.hpp`、`hd_tail.hpp`、`hinge.hpp`）。照旧的
`main` 路径取文件会 404。

同样不要用 `vela-v4.1` 标签：那个标签仍带着 **v4** 的 `native` 目录
（`inference.hpp` 是 7962 B 的旧版，读不了 COMPACT48 权重）。

v4.1 源码钉在 **`f6ca2934`**（2026-09-28，v4.2 之前最后一次改动 `native` 的提交）：

```text
https://raw.githubusercontent.com/alfm201/adventure/f6ca2934/src/policies/vela/native/<file>
```

需要的文件：`core.hpp`、`compact.hpp`、`inference.hpp`、`bridge.cpp`、`tables.hpp`、
`deck-coefficients.hpp`。其中 `compact.hpp` 与 `core.hpp` 是 v4.1 新增的。

### v4.2 的源码（9 个文件）

v4.2 取自 `main` 的 **`bc0e2fc6`**（2026-10-04，落地 v4.2 的那次提交）：

```text
https://raw.githubusercontent.com/alfm201/adventure/bc0e2fc6/src/policies/vela/native/<file>
```

需要 `bridge.cpp`、`hinge.hpp`、`engine_factor.hpp`、`compact_factor.hpp`、`core.hpp`、
`exact_dp.hpp`、`hd_tail.hpp`、`tables.hpp`、`deck-coefficients.hpp`
（**含 `#include` 闭合，正好 9 个**）。v4.2 **删除了** `compact.hpp` 与 `inference.hpp`。

`extern "C"` 接口与 v4.1 **同名同签名**（`state_ptr` / `values_ptr` / `init_model` /
`evaluate_state` / `last_error`），所以 `pybind_module.cpp` 只在模块名与错误文案上有别。
v4.2 新增的 `last_depth()` / `last_capped()` 在上游 bridge 里返回写死的 `2` / `0`，
不带信息，故未导出。

> 版本对照：`tables.hpp` 与 `deck-coefficients.hpp` 在 v4.1 与 v4.2 之间**完全未变**
> （blob SHA 相同）；`core.hpp` 1683 → 2862 B，`bridge.cpp` 也变了。工程侧的完整
> 说明（各文件职责、构建脚本、模型哈希）由本项目自行维护，不指向其它仓库。


## 为什么提交二进制而不是只放源码

`.pyd` 分发给终端用户时是**必要运行时依赖**，不是可再生的构建中间产物——
用户机器上没有 MSVC / pybind11，也不该要求他们编译。上游模型本身是开源的，
所以这里提交的是「上游源码的一份可执行编译」，不含任何私有逻辑。

## 构建（需要时自行重编）

需要一个装了 `pybind11` 的 Python 环境 + Visual Studio Build Tools。
上游的源码与 `setup.py` 在此仓库中未镜像，请从上游项目获取（v4.1 **按上面的
`f6ca2934` 路径取**、v4.2 用 `bc0e2fc6`，**都不要用 `main`**）后自行编写 `setup.py`：
把 `pybind_module.cpp` 编成 `vela_official_v41` / `vela_official_v42` 扩展，
源文件只需 `pybind_module.cpp`（它 `#include "bridge.cpp"`）。

要点：
- **Python 版本必须与运行环境一致**。本项目便携版用 **Python 3.11**，
  所以文件名是 `vela_official_v41.cp311-win_amd64.pyd`（v4.2 同理）。用 3.12 编出来的
  `cp312` 在 3.11 里无法 import。
- v4 默认是 **CPU** 后端，可选 HIP 加速见上游 README（需要 ROCm 与
  `HIP_VISIBLE_DEVICES=1`）。v4.1 / v4.2 只有 CPU。
- v4.2 上游源码用 `if constexpr` 与 inline 变量，**`-std=c++17` 足够**（`.hpp` 里出现的
  `requires` 只是错误信息文本，不是 C++20 concept）。MSVC 需要补
  `__builtin_ctz` / `__builtin_popcount` 两个内建（与 v4.1 相同）。
- 本仓库不镜像上游源码，避免与上游许可产生冲突。

## 加载路径

`zephie_rolling_on.decision_models.vela_package` 按以下顺序查找扩展：

1. 已安装的同名模块
2. 项目根下 `native/vela/`（本目录）
3. 可执行文件同级目录（便携包解压后的布局）

查找用的是 `<模块名>.*.pyd` 这种**带点**的模式：不带点的 `vela_official*` 会同时
匹配到 `vela_official_v41` / `vela_official_v42`，从而加载错误的引擎。

找不到时 `import_model` 会返回明确错误，不会静默失败。
