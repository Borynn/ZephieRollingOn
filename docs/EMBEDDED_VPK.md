# 包内引擎 API v1

新版软件支持 `engine_type="python-v1"`。VPK 携带可读的 Python 引擎源码、权重和其他资源，
软件不包含 LYRA 专用特征或决策算法。旧 VELA VPK 不用转换，仍由原来的原生模块加载。
首次使用新格式需要更新一次软件；以后兼容 API v1 的引擎可独立发包。

共享依赖为 Python 3.11、NumPy、ONNX Runtime。Windows 发行包采用 DirectML 版本，
不需要用户安装 Python、CUDA 或 ONNX Runtime。依赖升级、新的本机扩展或不同系统要求仍可能需要更新软件。
该格式执行可信的本地代码，包内哈希只检查损坏，不证明发布者身份；只导入可信来源的包。
扫描模型和查看模型卡片不执行引擎，点击导入时才执行。

## 文件格式

沿用 VPK 外层：小端 uint32 头部字节数、UTF-8 JSON、busy PNG、done PNG、payload。
头部 `format="vela-package"`、`format_version=1`，新增字段：

- `engine_type="python-v1"`、`engine_api=1`、`engine_entry="engine.py"`。
- `payload_bytes` 和 `payload_sha256`：ZIP 的精确长度与 SHA-256。
- `resources_sha256`：每个 ZIP 成员名到 SHA-256 的映射，必须完整覆盖且不能重名。
- `model_id`、`name`、`infer_time`、`performance`：沿用模型卡片字段。
- `busy_png_bytes`、`done_png_bytes`：不带展示图时为 0。

ZIP 包含引擎入口和模型资源，展开总量最多 512 MiB；禁止绝对路径和 `..`。
资源只作为字节读取，不写入模型临时文件。不支持相对导入额外 Python 源文件；引擎入口应自包含。
可用 `python scripts/build_engine_vpk.py --help` 创建包。

## 引擎入口

入口提供 `create_engine(context)`，返回包含 `load()`、`decide(**kwargs)`、`close()` 的对象。
`load()` 返回 False 或抛异常表示失败；`close()` 应释放推理会话。
`context` 提供：

- `api_version`（1）、`model_id`、`settings`（字典）。
- `read_resource(name) -> bytes`：读取包内资源。
- `progress(percent, message)`：上报导入进度。
- `backend_changed(info)`：更新实际设备，字典可含 `device`、`label`、`provider`、`fallback_reason`。

`decide` 参数与 [模型调用契约](MODEL_PACKAGE_CONTRACT.md) 相同，返回含
`action`、`rl_action`、`native_code`、`v_cells` 的字典。动作词表顺序见
`vela_package._CARD_VOCAB`，与 `NATIVE_CARD_IDS` 的顺序不同。
估值必须有限；卡牌动作和槽位必须对应输入手牌。调用被串行化。
模型切换时关闭旧会话。引擎不能依赖其他模型包的内部实现。

## LYRA v1

LYRA 的 VPK 包含引擎、FP32 ONNX、特征表和上游许可，采用 V20 / pi20 一步评估。
默认首先尝试 DirectML 适配器 0，覆盖受支持的 AMD、Intel、NVIDIA DirectX 12 GPU。
独显/核显存在并不等于驱动支持 DirectML；初始化、自检或后续推理失败时自动切换 CPU。
同一次决策遇到 GPU 错误会从头在 CPU 重算；切换后保持 CPU，避免反复失败。
模型导入信息和建议中显示实际设备。多 GPU 机器不承诺选择最快的适配器。
底层运行时可能把不受 GPU 支持的算子分配到 CPU；GPU 标签表示 DirectML 已启用。

来源：https://github.com/alfm201/adventure ，冻结提交
`f173abf7f502e5969917f81a1bd02745d7e834f8`。包内 `SOURCE.json` 和 `LICENSES.txt`
记录来源及许可；引擎源码在 ZIP 的 `engine.py` 中。
