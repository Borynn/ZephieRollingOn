# ZephieRollingOn — directory map

Package: `zephie_rolling_on` · Dist: `zephie-rolling-on`
License: MIT（范围豁免见 `LICENSE` / `NOTICE`：闭源 `.zm` 与第三方内容不在 MIT 内）

| Path | Purpose |
|------|---------|
| `src/zephie_rolling_on/ui/` | Control panel, theme, hotkeys, model picker |
| `src/zephie_rolling_on/ui/system_scale.py` | System display-scale detection; blocks startup unless 100% (recognition needs logical == physical pixels) |
| `src/zephie_rolling_on/ui/log_export.py` | "导出日志" button: bundles log + live self-check + environment info + config snapshot into a zip in the program root |
| `src/zephie_rolling_on/app/` | Session loop, tick, model decide, runtime state |
| `src/zephie_rolling_on/executor/` | Click, replenish, mouse shield |
| `src/zephie_rolling_on/vision/` | Capture, OCR, templates, deck scan, calibration |
| `src/zephie_rolling_on/vision/image_io.py` | Unicode-safe image read/write (Windows OpenCV ANSI path limitation) |
| `src/zephie_rolling_on/vision/platinum_hammer.py` | Detect the platinum-hammer reward icon (used by the「不跳过白金锤子」mode) |
| `src/zephie_rolling_on/models/` | Game state, cards, map board |
| `src/zephie_rolling_on/data/` | Config loaders |
| `src/zephie_rolling_on/decision_models/` | Model package discovery, host pipe client, shared contract types |
| `src/zephie_rolling_on/diagnose.py` | Standalone environment self-check (manual; not run on startup) |
| `src/zephie_rolling_on/decision_models/types.py` | `DecisionModelPackage` protocol, card vocabulary, result types |
| `src/zephie_rolling_on/decision_models/model_host_client.py` | Packages served by `model_host.exe`; owns the host process |
| `src/zephie_rolling_on/decision_models/vela_package.py` | Packages loaded natively in-process (same protocol) |
| `src/zephie_rolling_on/paths.py` | Project root (source + frozen exe) |
| `native/vela/` | Native model engines (CPU): v4 `vela_official` + v4.1 `vela_official_v41`; required to import `.vpk` |
| `decision_models/` | Model packages: `zephie_m1_points.zm` (served by `model_host.exe`) + `vela_v4.1.vpk` (in-process). Dispatch is by extension; the UI shows them uniformly. `vela_v4.vpk` (~1 GB) ships as a release asset, not in the repo |
| `model_host.exe` | Runtime component that serves `.zm` packages (compiled, committed here); required for a clone to run closed models |
| `config/` | Regions, click targets, auto-click, … |
| `config/auto_click.default.yaml` | Factory defaults for auto-click settings (shipped, tracked) |
| `config/auto_click.yaml` | Per-user auto-click settings (gitignored; survives an overwrite update) |
| `assets/` | UI / card / OCR templates |
| `map.xlsx` / `map_info.txt` | Board map |
| `docs/MODEL_PACKAGE_CONTRACT.md` | Model package interface (shared by all formats) |
| `docs/PACKAGING.md` | Portable exe packaging for end users |
| `docs/USER_README_ZH.txt` | End-user readme copied into the portable zip |
| `scripts/setup_conda_env.ps1` | Create/update the conda env |
| `scripts/build_portable.ps1` | PyInstaller onedir → distributable folder (headless OpenCV, slim extras) |
| `scripts/check_gitignore.py` | Verify `.gitignore` rules (notably that `.pyd` is not caught by `*.py[cod]`) |
| `scripts/audit_independence.py` | Verify the project references nothing private (source text and committed binaries). The banned-string list is a **gitignored** sidecar (`audit_terms.local.py`); absent → pattern checks skipped |
| `scripts/audit_terms.local.py` | **Gitignored**: the private term list itself (it is the sensitive part; see the script's docstring) |
| `scripts/check_image_io.py` | Reject raw `cv2.imread`/`imwrite` (breaks on non-ASCII paths) |
| `scripts/pyi_rth_tcl_tk.py` | PyInstaller runtime hook: pin bundled Tcl/Tk paths |
| `assets/ui/zehpie_window_icon.png` | Window icon (used at runtime by the theme) |
| `assets/ui/scale_warning.png` | Illustration for the non-100%-display-scale startup notice |
| `assets/ui/platinum_hammer.png` | Template for the platinum-hammer reward; required by the build script and CI (the「不跳过白金锤子」mode degrades silently without it) |
| `assets/ui/zehpie_window_icon.ico` | Exe icon, embedded by `build_portable.ps1` |
| `environment.yml` | Conda env `zephie_rolling_on` |
| `pyproject.toml` / `requirements.txt` | Packaging + dependencies |
| `requirements.lock` | Exact pins for reproducible builds (full tree incl. PyInstaller/setuptools); used by CI |
| `scripts/check_lock.py` | Verify `requirements.lock` still satisfies every floor in `requirements.txt` |
| `scripts/check_undefined_names.py` | Scope-aware undefined-name check (AST). `py_compile` and import both miss these; in a Tk callback the `NameError` is silently dropped. Wired into `build_portable.ps1` and CI |
| `LICENSE` | MIT（含范围豁免：闭源 `.zm` 与第三方内容不在 MIT 内） |
| `NOTICE` | 第三方署名、游戏素材版权声明、免责说明；随分发包一起发出 |
| `.github/workflows/release.yml` | 推 `v*` 标签自动构建便携包并创建 Release |

## Run

```bash
conda activate zephie_rolling_on
python -m zephie_rolling_on.ui
```
