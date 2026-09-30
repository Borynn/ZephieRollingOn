# 便携打包

目标：解压即可用，无需安装。模型包单独分发，不打进包里。

## 推荐工具

PyInstaller **onedir**（文件夹模式）：生成 `exe` + `_internal`（依赖 DLL/pyd）+ 旁路资源目录。

## 体积（预期）

主要体积来自视觉栈，不是 Tk 界面本身：

| 依赖 | 作用 | 瘦身策略 |
|------|------|----------|
| `opencv-python-headless` | 模板匹配 / OCR 预处理 | 用 headless，去掉 ffmpeg/GUI（API 与完整版相同） |
| `onnxruntime` + RapidOCR | 数字 OCR | 去掉 quantization/tools；模型仍需打包（引擎初始化会加载） |
| NumPy / Pillow | 数组与 UI 图 | 保留 |

构建脚本还会：生成 exe 图标、钉死 Tcl/Tk 路径（避免 conda base 8.6.15 与环境 8.6.13 冲突）。

打包出的 exe 带 `requireAdministrator` 清单（PyInstaller `--uac-admin`）：
窗口截屏、输入注入、游戏窗口访问都需要管理员权限，权限不足时点击会被游戏静默丢弃。
用户启动时会看到「用户账户控制」弹窗，这是预期行为。

## 构建

在已安装依赖的环境中：

```powershell
conda activate zephie_rolling_on
pip install pyinstaller
powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
```

需要分发 `.zm` 模型时，把 `model_host.exe` 放在项目根或 `dist/`，或用 `-ModelHostExe` 指定路径。

输出目录：`dist/ZephieRollingOn_v<版本>/`（版本号取自 `scripts/build_portable.ps1`）。

## 分发方式

把整个 `ZephieRollingOn_v<版本>` 文件夹打成 zip 即可上传。
用户解压后应得到**一个文件夹**，里面有 `ZephieRollingOn!.exe` 与 `_internal/` 等；
**不要**只抽出 exe 单独发。

```text
ZephieRollingOn_v<版本>/        # 解压后的根目录（名可自定）
  ZephieRollingOn!.exe          # 双击启动
  model_host.exe                # 解释 .zm 包的运行时组件
  _internal/                    # Python / 运行库（勿删）
  native/vela/                  # 解释 .vpk 包的原生引擎（v4 + v4.1 两个）
  config/                       # 默认配置（可被运行时改写）
  assets/                       # 模板与图标
  map.xlsx
  map_info.txt
  decision_models/              # 模型包目录（构建时不打包含）
    README.md
  README_ZH.txt
  LICENSE
```

模型包**不再需要用户手动下载**：`vela_v4.1.vpk`（约 38 MB）与
`zephie_m1_points.zm`（约 17 MB）已打进分发包，解压即可用。

打包脚本会先清空 `decision_models/`，再只复制 `$BundleModels` 列出的包——
这样源码目录里残留的其他包不会意外混进发布件。

体积较大的模型（`vela_v4.vpk`，约 1 GB）仍按发布页单独提供，需要时放进
`decision_models/` 后点「选择决策模型」导入。

构建时会排除开发机上的**用户状态文件**（`config/` 下的 `auto_click.yaml`、
`ui_geometry.yaml`、`game_window.yaml`、`planner_state.yaml`、`hotkeys.yaml`），
避免把构建者的个人设置当成出厂默认发出去；出厂值来自
`config/auto_click.default.yaml`，它在更新时会被覆盖，用户改过的
`config/auto_click.yaml` 则被保留。

若资源管理器仍显示旧图标：关掉该文件夹窗口后重开，或把 exe 复制到新路径再看
（Windows 图标缓存有时不刷新）。

## 用户更新方式

| 更新内容 | 做法 |
|----------|------|
| 模型包 | 只替换/新增 `decision_models/` 下的包，界面点「导入」 |
| 脚本界面 | 用新压缩包覆盖解压目录（可保留用户已改的 `config/` 与已下的模型） |

## 自动发布（GitHub Actions）

`.github/workflows/release.yml`：推送 `v*` 标签时自动构建便携包并创建 Release。

- Python **3.11**（`setup-python`）——必须与内置引擎的 `cp311` ABI 一致，
  否则构建能过、运行时才失败
- 先校验标签与 `scripts/build_portable.ps1` 里的 `$Version` 一致，避免发布名不符实的产物
- 再校验运行时组件齐全（`model_host.exe`、两个引擎、`map.xlsx`、`LICENSE`、`NOTICE`、两个模型包）
- 构建 → 打成 `ZephieRollingOn_v<版本>.zip` → 上传 artifact → 创建 Release 并附带 zip

发布新版本：改好 `$Version`（以及 `pyproject.toml`、`__init__.py`）后打标签 `v<版本>` 并推送。

## 源码仓

仓库不提交大模型包（见 `.gitignore`）；`vela_v4.1.vpk`（约 38 MB）随仓库分发，
`vela_v4.vpk`（约 1 GB）按发布页单独提供。
`scripts/audit_independence.py` 可校验源码不引用任何私有内容；它依赖一份
**不入库**的词表 `scripts/audit_terms.local.py`，缺失时会跳过模式检查并正常退出
（公开副本本就没有私有内容可泄漏）。
