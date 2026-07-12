# coding: utf-8
"""
CapsLearn 学习端 —— 每周把 corrections.jsonl 里的「转写→终稿」配对挖一遍：

1. 同一处替换出现 ≥2 次 → 自动追加进 hot.txt（正确词 | 误写），下次听写直接替换；
2. 只出现 1 次的替换 → 列入候选，写进周报供人工确认；
3. 大段增删（风格性修改）→ 抽样进周报，攒够了用于润色 prompt 的 few-shot。

改 hot.txt 前自动备份到 learn/backups/，周报写到 learn/reports/。
计划任务每周日 20:00 运行；也可手动跑：python capslearn_miner.py [--dry-run] [--all]
"""
import argparse
import difflib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
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
AUTO_ADD_MIN = 2      # 出现多少次自动写入 hot.txt
MAX_TERM_LEN = 16     # 替换片段的最大长度（太长不是术语，是改写；英文单词常超 10 字符）
MARKER = '# ====== 自学习（capslearn 自动追加） ======'


def log(base: Path, msg: str):
    with open(base / 'learn' / 'miner.log', 'a', encoding='utf-8') as f:
        f.write(f'{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n')


CONTENT_CH = re.compile(r'[A-Za-z0-9一-鿿]')
EXPAND_MAX = 6  # 替换片段向两侧最多扩展多少个相同字符，以还原完整术语


def has_content(s: str) -> bool:
    return bool(CONTENT_CH.search(s))


def norm_light(s: str) -> str:
    return re.sub(r'\s+', '', s)


def trim_edges(s: str) -> str:
    return re.sub(r'^[^A-Za-z0-9一-鿿]+|[^A-Za-z0-9一-鿿]+$', '', s)


def strip_common_affixes(a: str, b: str):
    """去掉两串的公共前后缀，得到稳定的 diff 核心。"""
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    j = 0
    while j < len(a) - i and j < len(b) - i and a[len(a) - 1 - j] == b[len(b) - 1 - j]:
        j += 1
    return a[i:len(a) - j], b[i:len(b) - j]


def extract(asr: str, fin: str):
    """从一对文本里抽出替换对 (核心误写, 核心正确, 扩展误写, 扩展正确)。

    核心 = 纯 diff 片段，跨句子稳定，用来聚合频次；
    扩展 = 向两侧补齐相邻的相同内容字符（还原完整术语，如 马乏→玛伐
    还原为 马乏凯泰→玛伐凯泰），用来写进 hot.txt。
    """
    sm = difflib.SequenceMatcher(None, asr, fin)
    reps, style = [], False
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'replace':
            k = 0
            while (k < EXPAND_MAX and i1 > 0 and j1 > 0
                   and asr[i1 - 1] == fin[j1 - 1] and CONTENT_CH.match(asr[i1 - 1])):
                i1 -= 1; j1 -= 1; k += 1
            k = 0
            while (k < EXPAND_MAX and i2 < len(asr) and j2 < len(fin)
                   and asr[i2] == fin[j2] and CONTENT_CH.match(asr[i2])):
                i2 += 1; j2 += 1; k += 1
            ea, eb = trim_edges(asr[i1:i2]), trim_edges(fin[j1:j2])
            if not (ea and eb and ea != eb
                    and len(ea) <= MAX_TERM_LEN and len(eb) <= MAX_TERM_LEN
                    and norm_light(ea) != norm_light(eb)):
                continue
            ca, cb = strip_common_affixes(ea, eb)
            if not (ca and cb):
                continue
            # ea = prefix + ca + suffix（strip_common_affixes 的逆运算），
            # prefix/suffix 是本次出现里核心两侧的上下文扩展
            i = 0
            while i < len(ea) and i < len(eb) and ea[i] == eb[i]:
                i += 1
            j = 0
            while j < len(ea) - i and j < len(eb) - i and ea[len(ea) - 1 - j] == eb[len(eb) - 1 - j]:
                j += 1
            prefix = ea[:i]
            suffix = ea[len(ea) - j:] if j else ''
            reps.append((ca, cb, prefix, suffix))
        elif tag == 'insert' and has_content(fin[j1:j2]):
            style = True
        elif tag == 'delete' and has_content(asr[i1:i2]):
            style = True
    return reps, style


def load_hot_aliases(hot_path: Path) -> set:
    aliases = set()
    if not hot_path.exists():
        return aliases
    for line in hot_path.read_text(encoding='utf-8').splitlines():
        t = line.strip()
        if not t or t.startswith('#'):
            continue
        aliases.update(x.strip() for x in t.split('|') if x.strip())
    return aliases


def update_hot(hot_path: Path, additions, backups_dir: Path, dry_run: bool):
    """additions: [(误写, 正确)]。返回实际写入的 [(正确, 误写, 方式)]。"""
    lines = hot_path.read_text(encoding='utf-8').splitlines()
    alias_all, first_tok = set(), {}
    for idx, line in enumerate(lines):
        t = line.strip()
        if not t or t.startswith('#'):
            continue
        toks = [x.strip() for x in t.split('|') if x.strip()]
        if not toks:
            continue
        alias_all.update(toks)
        first_tok.setdefault(toks[0], idx)

    added = []
    for wrong, right in additions:
        if wrong in alias_all:
            continue  # 这个误写已被某条热词覆盖
        if right in first_tok:
            i = first_tok[right]
            lines[i] = lines[i].rstrip() + f' | {wrong}'
            added.append((right, wrong, '追加别名'))
        else:
            if MARKER not in lines:
                lines += ['', MARKER]
            lines.append(f'{right} | {wrong}')
            first_tok[right] = len(lines) - 1
            added.append((right, wrong, '新词条'))
        alias_all.add(wrong)
        alias_all.add(right)

    if added and not dry_run:
        backups_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hot_path, backups_dir / f'hot.txt.{datetime.now():%Y%m%d-%H%M%S}.bak')
        hot_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return added


def main():
    ap = argparse.ArgumentParser(description='CapsLearn 学习端')
    ap.add_argument('--base', default=None, help='CapsWriter 根目录（默认自动向上探测）')
    ap.add_argument('--dry-run', action='store_true', help='只分析不写任何文件')
    ap.add_argument('--all', action='store_true', help='忽略进度，重新处理全部配对')
    args = ap.parse_args()
    base = find_base(args.base)
    learn = base / 'learn'
    corrections = learn / 'corrections.jsonl'
    state_file = learn / 'miner_state.json'

    if not corrections.exists():
        log(base, 'RUN no corrections.jsonl, nothing to do')
        print('no corrections.jsonl yet')
        return

    all_lines = corrections.read_text(encoding='utf-8').splitlines()
    start = 0
    if not args.all and state_file.exists():
        try:
            start = json.loads(state_file.read_text(encoding='utf-8')).get('processed_lines', 0)
        except Exception:
            start = 0
    new_lines = all_lines[start:]
    if not new_lines:
        log(base, f'RUN nothing new (processed={start})')
        print('nothing new since last run')
        return

    rep_counter = Counter()          # key = (核心误写, 核心正确)
    prefixes = {}                    # key -> 各次出现的前缀扩展列表
    suffixes = {}                    # key -> 各次出现的后缀扩展列表
    style_samples = []
    n_pairs = 0
    for line in new_lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        asr, fin = rec.get('asr_text', ''), rec.get('final_text', '')
        if not asr or not fin:
            continue
        n_pairs += 1
        reps, style = extract(asr, fin)
        for ca, cb, prefix, suffix in reps:
            key = (ca, cb)
            rep_counter[key] += 1
            prefixes.setdefault(key, []).append(prefix)
            suffixes.setdefault(key, []).append(suffix)
        if style and len(style_samples) < 10:
            style_samples.append((asr, fin))

    def common_suffix_of(strs):
        s = min(strs, key=len)
        while s and not all(x.endswith(s) for x in strs):
            s = s[1:]
        return s

    def common_prefix_of(strs):
        s = min(strs, key=len)
        while s and not all(x.startswith(s) for x in strs):
            s = s[:-1]
        return s

    # 术语判定：只有所有出现里都紧邻核心的字符才算术语的一部分，
    # 只出现一次的上下文（如"患者服用"）被交集自动剔除
    known = load_hot_aliases(base / 'hot.txt')
    items = []
    for key, c in rep_counter.items():
        ca, cb = key
        p = common_suffix_of(prefixes[key])   # 前缀取「贴着核心的公共尾部」
        s = common_prefix_of(suffixes[key])   # 后缀取「贴着核心的公共头部」
        wrong, right = p + ca + s, p + cb + s
        # 超长时先砍上下文，保核心
        while len(wrong) > MAX_TERM_LEN or len(right) > MAX_TERM_LEN:
            if s:
                s = s[:-1]
            elif p:
                p = p[1:]
            else:
                break
            wrong, right = p + ca + s, p + cb + s
        if len(wrong) < 2 or len(right) < 2:
            continue
        if ca in known or wrong in known:
            continue
        items.append(((wrong, right), c))
    auto = [(w, r) for (w, r), c in items if c >= AUTO_ADD_MIN]
    candidates = [(w, r, c) for (w, r), c in items if c < AUTO_ADD_MIN]

    added = update_hot(base / 'hot.txt', auto, learn / 'backups', args.dry_run)

    # ---- 周报 ----
    now = datetime.now()
    report_dir = learn / 'reports'
    lines_out = [
        f'# CapsLearn 周报 {now:%Y-%m-%d %H:%M}',
        '',
        f'- 本次处理配对：{n_pairs} 对（累计 {start + len(new_lines)} 行）',
        f'- 自动写入 hot.txt：{len(added)} 条',
        f'- 待人工确认候选：{len(candidates)} 条',
        '',
    ]
    if added:
        lines_out.append('## 已自动写入 hot.txt')
        lines_out += [f'- {r} | {w}（{how}）' for r, w, how in added]
        lines_out.append('')
    if candidates:
        lines_out.append('## 候选（只出现 1 次，确认后可手动加进 hot.txt）')
        lines_out += [f'- {r} | {w}' for w, r, c in sorted(candidates, key=lambda x: -x[2])[:30]]
        lines_out.append('')
    if style_samples:
        lines_out.append('## 风格性修改抽样（攒给润色 prompt 用）')
        for asr, fin in style_samples:
            lines_out.append(f'- 转写：{asr}')
            lines_out.append(f'  终稿：{fin}')
        lines_out.append('')

    if not args.dry_run:
        report_dir.mkdir(parents=True, exist_ok=True)
        report = report_dir / f'{now:%Y-%m-%d_%H%M}.md'
        report.write_text('\n'.join(lines_out), encoding='utf-8')
        state_file.write_text(json.dumps({'processed_lines': start + len(new_lines)}), encoding='utf-8')
        log(base, f'RUN pairs={n_pairs} added={len(added)} candidates={len(candidates)} report={report.name}')
    else:
        print('\n'.join(lines_out))
        log(base, f'DRY-RUN pairs={n_pairs} would-add={len(added)}')


if __name__ == '__main__':
    main()
