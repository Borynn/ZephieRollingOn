# 幸运卡模板资源

幸运卡模板 PNG（按条图间隙手工切分），供后续模板匹配识别槽位内卡片。

## 目录

| 目录 | 内容 |
|------|------|
| `templates/next/` | NEXT 跳关卡（1 张） |
| `templates/multiply/` | ×2、×3、×5、×7、×8、×10 倍数骰子卡（6 张） |
| `templates/step_forward/` | +1–+12 前进步数卡（12 张） |
| `templates/step_back/` | -1–-3 后退步数卡（3 张） |
| `manifest.yaml` | 卡 ID、类型、是否耗骰子、模板路径 |

## 规则摘要

- **步数卡**（`+` / `-`）：前进或后退指定格数，**不消耗**骰子。
- **倍数卡**（`×`）：使用 **1 个骰子**，点数乘以卡面数字。
- **NEXT**：传到下一关跳关终点，**不消耗**骰子。

## 游戏内 ROI

客户区：`left=760, top=860, width=220, height=40`（`lucky_cards_region`）

每格卡槽在裁剪图内的写死坐标见 `config/regions.yaml` → `lucky_card_slots`（由 `debug_lucky_slot.png` 标定，勿用等分切割）。
