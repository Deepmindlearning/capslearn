# coding: utf-8
"""
CapsLearn 采集端 —— 把「CapsWriter 原始转写」和「你改完的终稿」配成学习样本。

用法：在任意应用里改完一段听写文字后，选中最终版本，按 Pause 键（或 Ctrl+Alt+L）。
脚本自动复制选中文字，在最近的听写日记（D:\\CapsWriter-Offline\\年\\月\\日.md）里
模糊匹配出对应的原始转写，把配对追加到 learn/corrections.jsonl。
每周由 capslearn_miner.py 从配对里挖出热词与风格样本。

右下角会弹出 2 秒的小提示：绿=已记录，黄=没配上/没选中。
"""
import argparse
import difflib
import json
import queue
import re
import socket
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_BASE = Path(r'D:\CapsWriter-Offline')


def find_base(cli_value=None) -> Path:
    """定位 CapsWriter 根目录：命令行指定 > 从脚本/exe 所在位置向上找 > 默认值。"""
    if cli_value:
        return Path(cli_value)
    anchor = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
    for d in (anchor, *anchor.parents):
        if (d / 'hot.txt').exists() and (d / 'config_client.py').exists():
            return d
    return DEFAULT_BASE


def say(*args):
    """noconsole 打包后 stdout 可能不存在，print 需要兜底。"""
    try:
        print(*args)
    except Exception:
        pass
SIM_THRESHOLD = 0.45      # 终稿与转写的最低相似度，低于视为没配上
LOOKBACK_DAYS = 2         # 在最近几天的日记里找原始转写
LOOKBACK_ENTRIES = 150    # 最多回看多少条
SINGLETON_PORT = 48620    # 单实例锁端口

# 日记条目：[HH:MM:SS](audio) 文本   或   HH:MM:SS 文本
# 注意：音频路径本身含括号（如 assets/(20260711-1643)xx.wav），
# 但 diary_writer 已把路径中的空格转成 %20，故路径内必无空格，用 \S+ 匹配
ENTRY_RE = re.compile(r'^(?:\[(\d{2}:\d{2}:\d{2})\]\((\S+)\)|(\d{2}:\d{2}:\d{2}))\s+(.+)$')

COLORS = {'ok': '#1e7e34', 'warn': '#b8860b'}
ui_q = queue.Queue()


def log(base: Path, msg: str):
    with open(base / 'learn' / 'capture.log', 'a', encoding='utf-8') as f:
        f.write(f'{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n')


def notify(text: str, kind: str):
    ui_q.put((text, kind))


def load_recent_entries(base: Path):
    entries = []
    today = date.today()
    for delta in range(LOOKBACK_DAYS - 1, -1, -1):
        d = today - timedelta(days=delta)
        f = base / f'{d:%Y}' / f'{d:%m}' / f'{d:%d}.md'
        if not f.exists():
            continue
        for line in f.read_text(encoding='utf-8', errors='ignore').splitlines():
            m = ENTRY_RE.match(line.strip())
            if m:
                entries.append({
                    'date': f'{d:%Y-%m-%d}',
                    'time': m.group(1) or m.group(3),
                    'audio': m.group(2) or '',
                    'text': m.group(4).strip(),
                })
    return entries[-LOOKBACK_ENTRIES:]


def _norm(s: str) -> str:
    return re.sub(r'[\s，。,.、！？!?：:；;"\'（）()【】\[\]“”‘’…—-]+', '', s)


def best_match(selection: str, entries: list):
    ns = _norm(selection)
    best, best_sim = None, 0.0
    for e in entries:
        sim = difflib.SequenceMatcher(None, _norm(e['text']), ns).ratio()
        if sim > best_sim:
            best, best_sim = e, sim
    return best, best_sim


def record(base: Path, entry: dict, final_text: str, sim: float):
    rec = {
        'captured_at': datetime.now().isoformat(timespec='seconds'),
        'entry_date': entry['date'],
        'entry_time': entry['time'],
        'asr_text': entry['text'],
        'final_text': final_text,
        'audio': entry['audio'],
        'similarity': round(sim, 3),
    }
    with open(base / 'learn' / 'corrections.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def get_selection() -> str:
    """模拟 Ctrl+C 取当前选中文字，之后尽量恢复原剪贴板。"""
    import pyperclip
    from pynput.keyboard import Controller, Key
    kb = Controller()
    try:
        old = pyperclip.paste()
    except Exception:
        old = ''
    try:
        pyperclip.copy('')
    except Exception:
        pass
    time.sleep(0.05)
    with kb.pressed(Key.ctrl_l):
        kb.press('c')
        kb.release('c')
    text = ''
    for _ in range(20):  # 最多等 1 秒
        time.sleep(0.05)
        try:
            text = pyperclip.paste()
        except Exception:
            text = ''
        if text:
            break
    try:
        if old:
            pyperclip.copy(old)
    except Exception:
        pass
    return (text or '').strip()


_busy = threading.Lock()


def make_hotkey_handler(base: Path):
    def on_hotkey():
        if not _busy.acquire(blocking=False):
            return
        try:
            time.sleep(0.15)  # 给修饰键一点释放时间
            sel = get_selection()
            if not sel:
                notify('CapsLearn：没有选中文字', 'warn')
                log(base, 'MISS no-selection')
                return
            entries = load_recent_entries(base)
            if not entries:
                notify('CapsLearn：最近两天没有听写日记', 'warn')
                log(base, 'MISS no-diary')
                return
            e, sim = best_match(sel, entries)
            if e is None or sim < SIM_THRESHOLD:
                notify(f'CapsLearn：没配上（最高相似度 {sim:.0%}）', 'warn')
                log(base, f'MISS sim={sim:.2f} sel={sel[:40]!r}')
                return
            record(base, e, sel, sim)
            notify(f'CapsLearn：已记录 ✓ 相似度 {sim:.0%}', 'ok')
            log(base, f'PAIR sim={sim:.2f} entry={e["time"]} sel={sel[:40]!r}')
        except Exception as ex:
            log(base, f'ERROR {ex!r}')
        finally:
            _busy.release()
    return on_hotkey


def run_ui():
    """主线程跑一个隐藏的 tk root，轮询队列弹小提示。"""
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    win = tk.Toplevel(root)
    win.withdraw()
    win.overrideredirect(True)
    win.attributes('-topmost', True)
    label = tk.Label(win, text='', font=('Microsoft YaHei', 11),
                     fg='white', padx=14, pady=8)
    label.pack()
    hide_job = [None]

    def poll():
        try:
            while True:
                text, kind = ui_q.get_nowait()
                color = COLORS.get(kind, '#333333')
                label.config(text=text, bg=color)
                win.config(bg=color)
                win.update_idletasks()
                w, h = win.winfo_reqwidth(), win.winfo_reqheight()
                sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
                win.geometry(f'+{sw - w - 40}+{sh - h - 80}')
                win.deiconify()
                if hide_job[0]:
                    root.after_cancel(hide_job[0])
                hide_job[0] = root.after(2000, win.withdraw)
        except queue.Empty:
            pass
        root.after(150, poll)

    poll()
    return root


def main():
    ap = argparse.ArgumentParser(description='CapsLearn 采集端')
    ap.add_argument('--base', default=None, help='CapsWriter 根目录（默认自动向上探测）')
    ap.add_argument('--test', help='跳过热键与剪贴板，把该文本当作选中内容跑一遍配对')
    args = ap.parse_args()
    base = find_base(args.base)
    (base / 'learn').mkdir(parents=True, exist_ok=True)

    if args.test:
        entries = load_recent_entries(base)
        if not entries:
            say('no diary entries found')
            return
        e, sim = best_match(args.test, entries)
        say(f'entries={len(entries)} best_sim={sim:.2f}')
        if e and sim >= SIM_THRESHOLD:
            record(base, e, args.test, sim)
            say(f'recorded, asr_text={e["text"]!r}')
        else:
            say('no match above threshold')
        return

    # 单实例锁：端口占用说明已有实例在跑
    lock = socket.socket()
    try:
        lock.bind(('127.0.0.1', SINGLETON_PORT))
    except OSError:
        return

    from pynput import keyboard
    handler = make_hotkey_handler(base)
    hk = keyboard.GlobalHotKeys({'<pause>': handler, '<ctrl>+<alt>+l': handler})
    hk.daemon = True
    hk.start()
    log(base, 'START capture listener (Pause / Ctrl+Alt+L)')

    root = run_ui()
    root.mainloop()


if __name__ == '__main__':
    main()
