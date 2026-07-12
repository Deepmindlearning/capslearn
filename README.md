# CapsLearn

**让 [CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline) 从你的修改中学习** —— 一个本地自学习纠错回路：把「语音原始转写」和「你改完的终稿」配成语料，每周自动把反复出现的纠错学成热词。

> **English**: A self-learning correction loop for CapsWriter-Offline (a popular offline Chinese dictation tool for Windows). Pair the raw ASR transcript with your edited final text via one hotkey; a weekly miner turns recurring corrections into hotwords automatically. All data stays on your machine — unlike cloud dictation apps (Typeless, Wispr Flow), your correction corpus is yours: plain local files, ready for prompt few-shots today and model fine-tuning tomorrow.

## 为什么

云端听写工具（Typeless / Wispr Flow）的"个性化"锁在它们自己的服务器和界面里：转录历史不能统一导出，你在 Word / 微信里改掉的字它们永远学不到。CapsWriter-Offline 已经把每句听写（文本+音频）按日期归档在你本机——CapsLearn 补上缺的另一半：**你的终稿**，并把两者变成会自动生效的学习信号。

- 专业术语（医学、法律、代码……）反复被转错？改两次，第三次它就对了。
- 语料（音频 + 原始转写 + 终稿）全在本地纯文本/JSONL 里，将来微调模型就是现成训练集。

## 工作原理

```
听写 ──> CapsWriter 日记归档（原始转写 + 音频）
改稿 ──> 选中终稿按 Pause ──> capslearn_capture 模糊配对 ──> corrections.jsonl
每周 ──> capslearn_miner 挖掘 diff ──> 重复 ≥2 次的纠错自动写入 hot.txt（改前自动备份）
                                └──> 单次纠错和风格修改进周报，供人工确认
```

**为什么不全自动监视你的编辑？** Windows 上没有可靠手段跨任意应用读取"你后来改了什么"，硬做等于键盘记录器。所以采集是半自动（选中+按键，2 秒），学习是全自动（每周）。只有同一处纠错出现 ≥2 次才自动进热词，防止误学。

## 安装

前提：已安装 [CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline) v2.6+（Windows 10/11）。

```powershell
# 1. 放置：把本仓库放到 CapsWriter 根目录下的 learn\ 文件夹
#    （脚本默认按 D:\CapsWriter-Offline 布局，装在别处改脚本顶部 DEFAULT_BASE 即可）

# 2. 环境（Python 3.10+，两个依赖）
python -m venv learn\.venv
learn\.venv\Scripts\pip install pynput pyperclip

# 3. 启动采集端
learn\.venv\Scripts\pythonw.exe learn\capslearn_capture.py
```

开机自启（可选）：在启动文件夹（`Win+R` → `shell:startup`）放一个 `CapsLearn.vbs`：

```vbs
CreateObject("WScript.Shell").Run """D:\CapsWriter-Offline\learn\.venv\Scripts\pythonw.exe"" ""D:\CapsWriter-Offline\learn\capslearn_capture.py""", 0, False
```

每周自动学习（可选）：

```powershell
schtasks /create /tn "CapsLearnWeekly" /tr '"D:\CapsWriter-Offline\learn\.venv\Scripts\pythonw.exe" "D:\CapsWriter-Offline\learn\capslearn_miner.py"' /sc weekly /d SUN /st 20:00
```

## 用法

日常只有一个动作：**改完听写文字后，选中最终版本，按 `Pause` 键**（或 `Ctrl+Alt+L`）。

- 右下角绿色"已记录 ✓" = 配对成功
- 黄色 = 没选中文字 / 最近两天日记里没配上（默认回看 2 天，可改脚本顶部 `LOOKBACK_DAYS`）
- 不想让它学的修改，不按就是了

手动挖掘（不等周日）：

```powershell
learn\.venv\Scripts\python.exe learn\capslearn_miner.py            # 立即学一遍
learn\.venv\Scripts\python.exe learn\capslearn_miner.py --dry-run  # 只看不写
learn\.venv\Scripts\python.exe learn\capslearn_miner.py --all      # 重新处理全部语料
```

产出：

| 位置 | 内容 |
|---|---|
| `corrections.jsonl` | 配对语料（转写 → 终稿 + 音频路径） |
| `reports/*.md` | 周报：学了什么、候选词、风格修改抽样 |
| `backups/` | 每次修改 hot.txt 前的自动备份 |

热词即时生效：miner 写完 `hot.txt`，CapsWriter 客户端 3 秒内热重载。

## 挖掘算法要点

diff 碎片还原完整术语的三步：`difflib` 替换片段 → 向两侧扩展相邻相同字符 → **对同一核心的多次出现取公共扩展交集**（只有每次都紧邻核心的字符才算术语的一部分，单次出现的上下文被交集自动剔除）。这让「马乏凯泰→玛伐凯泰」在不同句子里出现两次后，学到的是完整药名而非上下文碎片。

## 隐私

一切都在本机：音频、转写、终稿、学习结果。`.gitignore` 已排除全部运行时数据——如果你 fork 本仓库，**不要**把 `corrections.jsonl`、`reports/`、日记归档提交上去。

## 致谢

- [CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)（Haujet Zhao, MIT）—— 本工具是它的伴生回路，日记归档格式与热词机制均来自该项目
- Built with [Claude Code](https://claude.com/claude-code)

## License

MIT © 2026 冯灿 (Feng Can)
