# -*- coding: utf-8 -*-
"""非 XQF 文本棋谱 -> 着法序列（中文着法解析，move_text 的逆运算）。
支持 .txt/.chn 等文本着法：回合号、红黑着法、前/后/中、进退平、
括号/方括号注解。解析后配合 xqf_write 生成 XQF。
"""
import re
import xqf

CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
RED_KIND = {'车': 'R', '马': 'N', '相': 'B', '仕': 'A', '帅': 'K', '炮': 'C', '兵': 'P'}
BLK_KIND = {'车': 'r', '马': 'n', '象': 'b', '士': 'a', '将': 'k', '炮': 'c', '卒': 'p'}
RED_NAMES = set('车马相仕帅炮兵')
BLK_NAMES = set('车马象士将炮卒')
DIAG = {'N': 2, 'n': 2, 'B': 2, 'b': 2, 'A': 1, 'a': 1}   # 马/象/士 进退用目标线


def _num(ch):
    if ch in CN:
        return CN[ch]
    if ch.isdigit():
        return int(ch)
    return None


def _norm(s):
    # 全角数字/标点 -> 半角
    out = []
    for c in s:
        o = ord(c)
        if 0xFF10 <= o <= 0xFF19:
            out.append(chr(o - 0xFF10 + ord('0')))
        elif c in '．。、':
            out.append('.')
        else:
            out.append(c)
    return ''.join(out)


def side_of_name(name):
    if name in '相仕帅兵':
        return True
    if name in '象士将卒':
        return False
    return None


def candidates(board, kind, fx=None):
    res = []
    for (x, y), (k, nm) in board.items():
        if k == kind and (fx is None or x == fx):
            res.append((x, y))
    return res


def parse_token(board, red, token):
    """返回 (from(x,y), to(x,y))。red 表示该着是否红方。"""
    t = token
    prefix = None
    if t[0] in '前后中':
        prefix = t[0]; t = t[1:]
    name = t[0]
    kind = (RED_KIND if red else BLK_KIND)[name]
    rest = t[1:]
    # rest 形如 "二平五" / "8进7" / "平5"(前/后时无线号)
    m = re.match(r'^([一二三四五六七八九1-9]?)([平进退])([一二三四五六七八九1-9])$', rest)
    if not m:
        raise ValueError('无法解析着法: %s' % token)
    fn_s, act, tg_s = m.groups()
    fn = _num(fn_s) if fn_s else None
    tg = _num(tg_s)
    # 目标/起点线号 -> x
    def file_x(n):
        return (9 - n) if red else (n - 1)
    fx = file_x(fn) if fn is not None else None
    # 候选子
    cands = candidates(board, kind, fx)
    if prefix in ('前', '后', '中') and fx is None:
        # 同名多子，按前进方向排序选 前/后/中
        allc = candidates(board, kind)
        # 仅取同线有多子的组
        byfile = {}
        for (x, y) in allc:
            byfile.setdefault(x, []).append((x, y))
        grp = None
        for x, lst in byfile.items():
            if len(lst) >= 2:
                grp = lst; break
        if grp is None:
            grp = allc
        grp = sorted(grp, key=lambda p: p[1], reverse=red)  # 红前=y大
        if prefix == '前':
            cands = [grp[0]]
        elif prefix == '后':
            cands = [grp[-1]]
        else:
            cands = [grp[len(grp) // 2]]
    if not cands:
        raise ValueError('找不到棋子: %s' % token)
    solutions = []
    for (x, y) in cands:
        if act == '平':
            tx = file_x(tg); ty = y
        elif kind in DIAG:
            tx = file_x(tg)
            dy = DIAG[kind]
            dx = abs(tx - x)
            # 马 dx=1->dy2, dx=2->dy1; 象 dx2 dy2; 士 dx1 dy1
            if kind in ('N', 'n'):
                need = 2 if dx == 1 else (1 if dx == 2 else None)
            elif kind in ('B', 'b'):
                need = 2 if dx == 2 else None
            else:
                need = 1 if dx == 1 else None
            if need is None:
                continue
            ty = y + need if (act == '进') == red else y - need
        else:
            tx = x
            steps = tg
            ty = y + steps if (act == '进') == red else y - steps
        if not (0 <= tx <= 8 and 0 <= ty <= 9):
            continue
        if (tx, ty) == (x, y):
            continue
        occ = board.get((tx, ty))
        if occ and occ[0].isupper() == red:   # 不能吃己方
            continue
        solutions.append(((x, y), (tx, ty)))
    if not solutions:
        raise ValueError('着法无合法落点: %s' % token)
    # 多解时优先唯一；若仍多解取第一个（通常前/后已消歧）
    return solutions[0]


MOVE_RE = re.compile(
    r'(?:[前后中][车马炮兵卒][平进退][一二三四五六七八九1-9])'
    r'|(?:[车马相仕帅炮兵象士将卒][一二三四五六七八九1-9][平进退][一二三四五六七八九1-9])')
COMMENT_RE = re.compile(r'[（(【\[{]([^）)】\]}]*)[）)】\]}]')


def tokenize(text):
    """按出现顺序产出 ('move', token) 与 ('note', str)。去掉回合号。"""
    text = _norm(text)
    # 去掉回合号: 数字+点(可能多个) 如 "1." "12..." "1.."
    text = re.sub(r'\d+\.{1,3}', ' ', text)
    events = []
    for m in MOVE_RE.finditer(text):
        events.append((m.start(), 'move', m.group()))
    for m in COMMENT_RE.finditer(text):
        events.append((m.start(), 'note', m.group(1).strip()))
    events.sort(key=lambda e: e[0])
    return [(k, v) for _, k, v in events]


def parse_text(text, init_board=None, red_first=True):
    """解析文本 -> dict(可交给 xqf_write.write)。init_board 默认标准开局。"""
    if init_board is None:
        init_board = {}
        arr = xqf_write_standard()
        for side in range(2):
            nm = xqf.RED_NAME if side == 0 else xqf.BLK_NAME
            for mi in range(16):
                p = arr[side * 16 + mi]
                if p == 0xFF:
                    continue
                k = xqf.PIECE_KIND[mi]
                init_board[(p // 10, p % 10)] = (k if side == 0 else k.lower(), nm[k])
    board = dict(init_board)
    moves = []
    red = red_first
    parent = -1
    pending_note = ''
    for kind, val in tokenize(text):
        if kind == 'note':
            if moves:
                moves[-1]['note'] = (moves[-1]['note'] + '\n' + val).strip()
            else:
                pending_note = (pending_note + '\n' + val).strip()
            continue
        frm, to = parse_token(board, red, val)
        piece = board[frm]
        text_mv = xqf.move_text(board, frm[0], frm[1], to[0], to[1])
        i = len(moves)
        moves.append({'i': i, 'parent': parent, 'from': list(frm), 'to': list(to),
                      'piece': piece, 'note': '', 'vars': [], 'text': text_mv,
                      'red': red, 'depth': i + 1})
        board[to] = board.pop(frm)
        parent = i
        red = not red
    d = {'info': {'title': '导入棋谱', 'version': 18, 'result': 0, 'type': 1},
         'init_board': init_board, 'moves': moves, 'game_note': pending_note}
    return d


def xqf_write_standard():
    import xqf_write
    return xqf_write.standard_board32()


def _read_text(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('gbk', 'replace')


def convert_file(src, dst, title=None):
    """非 XQF 文本棋谱 -> XQF。返回着法数。"""
    import xqf_write
    text = _read_text(src)
    if not MOVE_RE.search(_norm(text)):
        raise ValueError('未识别到中文着法（可能是二进制格式）: %s' % src)
    d = parse_text(text)
    if title:
        d['info']['title'] = title
    else:
        import os
        d['info']['title'] = os.path.splitext(os.path.basename(src))[0]
    xqf_write.write(d, dst, original_path=None)
    return len(d['moves'])


def batch_convert(folder, outdir=None, exts=('.txt', '.chn', '.ccm', '.mxq', '.che')):
    """批量转换文件夹内非 XQF 文本棋谱。返回 (成功列表, 失败列表)。"""
    import os
    outdir = outdir or folder
    os.makedirs(outdir, exist_ok=True)
    ok, fail = [], []
    for fn in sorted(os.listdir(folder)):
        if os.path.splitext(fn)[1].lower() not in exts:
            continue
        src = os.path.join(folder, fn)
        dst = os.path.join(outdir, os.path.splitext(fn)[0] + '.xqf')
        try:
            n = convert_file(src, dst)
            ok.append((fn, n))
        except Exception as e:
            fail.append((fn, str(e)))
    return ok, fail


if __name__ == '__main__':
    import sys
    # 往返测试：把 demo 的着法渲染成文本再解析回坐标，比对 from/to
    src = sys.argv[1] if len(sys.argv) > 1 else 'demo.xqf'
    d = xqf.annotate(xqf.parse(src))
    # 仅主线（沿 children 第一个）
    kids = {}
    for m in d['moves']:
        kids.setdefault(m['parent'], []).append(m)
    line = []
    cur = -1
    while kids.get(cur):
        m = kids[cur][0]
        line.append(m); cur = m['i']
    text = ' '.join('%d. %s' % ((i // 2) + 1, m['text']) if i % 2 == 0 else m['text']
                    for i, m in enumerate(line))
    d2 = parse_text(text, init_board=d['init_board'])
    bad = 0
    for a, b in zip(line, d2['moves']):
        if tuple(a['from']) != tuple(b['from']) or tuple(a['to']) != tuple(b['to']):
            bad += 1
            if bad <= 15:
                print('DIFF', a['text'], a['from'], a['to'], '->', b['text'], b['from'], b['to'])
    print('mainline moves=%d parsed=%d diffs=%d' % (len(line), len(d2['moves']), bad))
    print('TEXT IMPORT ROUNDTRIP', 'OK' if bad == 0 and len(line) == len(d2['moves']) else 'FAIL')