# Model package contract

模型包放在 `decision_models/`，界面用同一套接口驱动所有模型。

## 两种包格式

| 扩展名 | 由谁解释 |
|--------|----------|
| `zephie_<model_id>.zm` | `model_host.exe`（独立进程，命名管道通信） |
| `*.vpk` | 进程内的原生引擎（见 `native/vela/`） |

两者都实现同一协议，上层不关心区别。包格式与解释方式属于实现细节。

## 协议

| 方法 | 作用 |
|------|------|
| `display_info()` | 返回名称 / 推理参考时间 / 模型表现 |
| `import_model(on_progress=…)` | 加载模型，`on_progress(percent, message)` 上报进度 |
| `decide(...)` | 输入局面，返回动作与估值 |
| `import_art_png(which)` | 可选：返回内置的导入贴纸（`busy` / `done`） |

## 输入

- `cell_id`：当前格子编号
- `dice_remaining`：剩余骰子数
- `next_roll_free`：下一次投骰是否免费
- `hand`：手牌，最多 5 张，元素为 card id
- `drawn`：各 card id 的已抽次数（含手牌）

card id 顺序见 `zephie_rolling_on.decision_models.types.NATIVE_CARD_IDS`。

## 输出

- `action`：`"roll"` 或 card id
- `rl_action`：`1` = 投骰，`2..23` = 动作词表中的卡牌
- `native_code`：`0` = 投骰，`1..hand_len` = 手牌槽位（从 1 起）
- `v_cells`：该局面的估值，单位由模型自己定义

脚本把 `"roll"` 映射为 `roll_normal`，卡牌映射为 `use_<card_id>`。

## 放置

```text
decision_models/            # 模型包
model_host.exe              # 与主程序同级（解释 .zm 包）
native/vela/                # 原生引擎（解释 .vpk 包）
```

## 错误处理

- 包不可用时 discovery 跳过该文件，其余模型照常加载
- `import_model` 失败返回 `ok=False` 与原因
- `decide` 失败向调用方抛出异常
