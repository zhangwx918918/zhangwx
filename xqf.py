# -*- coding: utf-8 -*-
"""XQF (象棋演播室 XQStudio) 二进制棋谱解析器。

格式要点（依据 XQStudio 作者董世伟定义的 XQF 结构）:
  0    2s   "XQ"
  2    1    版本号 (>=12 时走子数据带加密)
  3    13   密钥区: KeyMask, ProductId(4), KeyOrA, KeyOrB, KeyOrC, KeyOrD,
                KeysSum, KeyXY, KeyXYf, KeyXYt
  16   32   32 个棋子的初始位置 (0xFF = 无子)
  ...       棋头文字字段 (题名/赛事/选手/结果...)
  1024      走子记录区 (version>=12 时逐字节加密)

走子记录: 每条 4 字节 [起点, 落点, 标志, 保留]，位置为 x*10+y。
标志 & 0xE0: 0x80 有后续着法, 0x40 有变着, 0x20 有注解。
"""
import struct

HEADER_SIZE = 1024
MOVE_FROM_OFFSET = 0x18
MOVE_TO_OFFSET = 0x20
FLAG_MASK = 0xE0
FLAG_HAS_NEXT = 0x80
FLAG_HAS_VAR = 0x40
FLAG_HAS_ANNO = 0x20
COPYRIGHT = b"[(C) Copyright Mr. Dong Shiwei.]"

# 32 子顺序: 每侧 16 子，回文排列
PIECE_KIND = "RNBAKABNRCCPPPPP"
RED_NAME = {'R': '车', 'N': '马', 'B': '相', 'A': '仕', 'K': '帅', 'C': '炮', 'P': '兵'}
BLK_NAME = {'R': '车', 'N': '马', 'B': '象', 'A': '士', 'K': '将', 'C': '炮', 'P': '卒'}


def _key_derive(raw_xy, raw_xyf, raw_xyt, raw_sum, mask, ors):
    """按 XQF 的公式算出四个解密因子。"""
    def core(b):
        t = (b * b) * 3 + 9
        t = t * 3 + 8
        t = t * 2 + 1
        return t * 3 + 8
    k_xy = (core(raw_xy) * raw_xy) & 0xFF
    k_xyf = (core(raw_xyf) * k_xy) & 0xFF
    k_xyt = (core(raw_xyt) * k_xyf) & 0xFF
    k_rmk = (((raw_sum * 256 + raw_xy) % 32000) + 767) & 0xFFFF
    fk = ((raw_sum & mask) | ors[0],
          (raw_xy & mask) | ors[1],
          (raw_xyf & mask) | ors[2],
          (raw_xyt & mask) | ors[3])
    keys32 = bytearray(COPYRIGHT)
    for i in range(32):
        keys32[i] &= fk[i % 4]
    return k_xy, k_xyf, k_xyt, k_rmk, keys32


def _decode_stream(keys32, buf):
    # 按 32 字节周期分组，用 bytes.translate(C 速度) 做逐字节减法，
    # 与逐字节循环完全等价；批量扫描数万 XQF 时快约两个数量级。
    out = bytearray(len(buf))
    for j in range(32):
        tbl = bytes((b - keys32[j]) & 0xFF for b in range(256))
        out[j::32] = buf[j::32].translate(tbl)
    return bytes(out)


def _decode_board(man_buff, version, k_xy):
    """返回 32 长度数组: index -> 位置(x*10+y) 或 0xFF。"""
    tmp = bytearray([0xFF] * 32)
    if k_xy is None:
        for i in range(32):
            tmp[i] = man_buff[i]
        return tmp
    for i in range(32):
        if version >= 12:
            tmp[(k_xy + i + 1) & 0x1F] = man_buff[i]
        else:
            tmp[i] = man_buff[i]
    for i in range(32):
        v = (tmp[i] - k_xy) & 0xFF
        tmp[i] = 0xFF if v > 89 else v
    return tmp


def _s(buf, ln):
    if ln <= 0:
        return ''
    return buf[:ln].split(b'\x00')[0].decode('gb18030', 'ignore').strip()


class Reader(object):
    def __init__(self, buf):
        self.buf = buf
        self.i = 0

    def take(self, n):
        r = self.buf[self.i:self.i + n]
        self.i += min(n, len(self.buf) - self.i)
        return r

    def u8(self):
        b = self.take(1)
        return b[0] if b else None

    def u32(self):
        b = self.take(4)
        if len(b) < 4:
            return 0
        return struct.unpack('<I', b)[0]


def parse(path):
    data = open(path, 'rb').read()
    if data[:2] != b'XQ':
        raise ValueError('不是 XQF 文件: %s' % path)
    version = data[2]
    (mask, _pid, oa, ob, oc, od, ksum, kxy_raw, kxyf_raw, kxyt_raw) = \
        struct.unpack('<BIBBBBBBBB', data[3:16])
    man_raw = data[16:48]

    if version >= 11:
        k_xy, k_xyf, k_xyt, k_rmk, keys32 = _key_derive(
            kxy_raw, kxyf_raw, kxyt_raw, ksum, mask, (oa, ob, oc, od))
    else:
        k_xy = k_xyf = k_xyt = k_rmk = None
        keys32 = None

    board = _decode_board(man_raw, version, k_xy if k_xy is not None else 0)

    # ---- 棋头文字 ----
    def fld(off, lenoff, stroff, ln):
        n = data[lenoff] if lenoff < len(data) else 0
        return _s(data[stroff:stroff + ln], n)

    info = {
        'version': version,
        'result': data[51],
        'type': data[64],
        'title': fld(80, 80, 81, 63),
        'event': fld(208, 208, 209, 63),
        'date': fld(272, 272, 273, 15),
        'place': fld(288, 288, 289, 15),
        'red': fld(304, 304, 305, 15),
        'black': fld(320, 320, 321, 15),
        'rule': fld(336, 336, 337, 63),
        'red_time': fld(400, 400, 401, 15),
        'black_time': fld(416, 416, 417, 15),
    }

    body = data[HEADER_SIZE:]
    if keys32 is not None:
        body = _decode_stream(keys32, body)
    r = Reader(body)

    # ---- 开局前的整局注解 ----
    head_flags = r.take(4)
    game_note = ''
    if version <= 0x0A:
        n = r.u32()
        game_note = r.take(n).decode('gb18030', 'ignore') if n else ''
    else:
        if head_flags and (head_flags[2] & FLAG_MASK) & FLAG_HAS_ANNO:
            n = r.u32() - k_rmk
            if n > 0:
                game_note = r.take(n).decode('gb18030', 'ignore')

    init = {}
    for side in range(2):
        nm = RED_NAME if side == 0 else BLK_NAME
        for mi in range(16):
            p = board[side * 16 + mi]
            if p == 0xFF:
                continue
            kind = PIECE_KIND[mi]
            init[(p // 10, p % 10)] = (kind if side == 0 else kind.lower(), nm[kind])

    moves = []
    pending = []          # 待读的变着子树: (parent_idx, board_before)
    parent_idx = -1
    board_state = dict(init)

    while True:
        st = r.take(4)
        if len(st) < 4 or not any(st):
            break
        bak = dict(board_state)
        if version <= 0x0A:
            has_next = bool(st[2] & 0xF0)
            has_var = bool(st[2] & 0x0F)
            n = r.u32()
            note = r.take(n).decode('gb18030', 'ignore') if n else ''
            frm = (st[0] - MOVE_FROM_OFFSET) & 0xFF
            to = (st[1] - MOVE_TO_OFFSET) & 0xFF
        else:
            fl = st[2] & FLAG_MASK
            has_next = bool(fl & FLAG_HAS_NEXT)
            has_var = bool(fl & FLAG_HAS_VAR)
            note = ''
            if fl & FLAG_HAS_ANNO:
                n = r.u32() - k_rmk
                if n > 0:
                    note = r.take(n).decode('gb18030', 'ignore')
            frm = (st[0] - MOVE_FROM_OFFSET - k_xyf) & 0xFF
            to = (st[1] - MOVE_TO_OFFSET - k_xyt) & 0xFF

        fx, fy = frm // 10, frm % 10
        tx, ty = to // 10, to % 10
        idx = len(moves)
        pid = board_state.get((fx, fy))
        moves.append({'i': idx, 'parent': parent_idx, 'from': [fx, fy], 'to': [tx, ty],
                      'piece': pid, 'note': note, 'vars': [], 'raw': list(st)})
        if pid is not None:
            board_state[(tx, ty)] = board_state.pop((fx, fy))

        # XQF 的存储顺序: 先写本步的后续着法链，再写本步的变着子树。
        # 变着是"本步的替代着"，因此挂在 parent_idx 下、走子前局面重新起算。
        if has_var:
            pending.append((parent_idx, bak))
        if has_next:
            parent_idx = idx
            continue
        if not pending:
            break
        parent_idx, board_state = pending.pop()

    # 挂变着树: 同一 parent 下的多条着法即为变着(第一条为主变)
    children = {}
    for m in moves:
        children.setdefault(m['parent'], []).append(m['i'])
    for k, v in children.items():
        if k >= 0:
            moves[k]['vars'] = v
    root_moves = children.get(-1, [])

    return {'info': info, 'init': {('%d,%d' % k): list(v) for k, v in init.items()},
            'moves': moves, 'roots': root_moves, 'game_note': game_note,
            'init_board': init}


RED_NUM = '一二三四五六七八九'
BLK_NUM = '123456789'
STRAIGHT = 'RCPK'          # 直行子: 车炮兵(卒)帅(将)


def _file_no(x, red):
    """纵线编号: 红方从右往左 1-9，黑方从其自身右往左 1-9。"""
    return (9 - x) if red else (x + 1)


def _num(n, red):
    return RED_NUM[n - 1] if red else BLK_NUM[n - 1]


def move_text(state, fx, fy, tx, ty):
    """按中国象棋传统记谱法生成一步的文字。state: {(x,y): (kind, name)}"""
    kind, name = state[(fx, fy)]
    red = kind.isupper()
    same_file = [(x, y) for (x, y), v in state.items() if v[0] == kind and x == fx]
    prefix = ''
    if len(same_file) > 1:
        # 同种棋子在同一纵线上，需要前/后(或前/中/后)区分
        order = sorted(same_file, key=lambda p: p[1], reverse=red)   # 红方: 前 = y 大
        pos = order.index((fx, fy))
        words = {2: ['前', '后'], 3: ['前', '中', '后'],
                 4: ['前', '二', '三', '后'], 5: ['前', '二', '三', '四', '后']}
        head = words.get(len(order), ['前'] * len(order))[pos] + name
    else:
        head = name + _num(_file_no(fx, red), red)
    act = '平' if ty == fy else ('进' if (ty > fy) == red else '退')
    if act == '平':
        tgt = _num(_file_no(tx, red), red)
    elif kind.upper() in STRAIGHT:
        tgt = _num(abs(ty - fy), red)
    else:
        tgt = _num(_file_no(tx, red), red)
    return head + act + tgt


def annotate(d):
    """给每条着法补上文字，并标注回合数/先后手。"""
    states = {-1: dict(d['init_board'])}
    for m in d['moves']:
        st = dict(states[m['parent']])
        fx, fy = m['from']
        tx, ty = m['to']
        if (fx, fy) in st:
            m['text'] = move_text(st, fx, fy, tx, ty)
            kind = st[(fx, fy)][0]
            m['red'] = kind.isupper()
            st[(tx, ty)] = st.pop((fx, fy))
        else:
            m['text'] = '?'
            m['red'] = None
        states[m['i']] = st
        # 深度 = 距根节点的步数
        m['depth'] = (d['moves'][m['parent']]['depth'] + 1) if m['parent'] >= 0 else 1
    return d


def board_fen(state, red_to_move=True):
    """局面 dict {(x,y):(kind,name)} → 象棋云库(chessdb.cn) FEN。

    行序: 黑方底线(y=9)在前、红方底线(y=0)在后；每行 x=0..8。
    棋子字母即 kind(红大写 R N B A K C P，黑小写)，空位压缩为数字。
    末尾 ' w'/' b' 表示轮走方，'- - 0 1' 为占位(云库忽略)。
    """
    rows = []
    for y in range(9, -1, -1):
        row = ''
        empty = 0
        for x in range(9):
            p = state.get((x, y))
            if p is None:
                empty += 1
            else:
                if empty:
                    row += str(empty)
                    empty = 0
                row += p[0]
        if empty:
            row += str(empty)
        rows.append(row)
    return '/'.join(rows) + (' w' if red_to_move else ' b') + ' - - 0 1'


def fen_to_board(fen):
    """FEN → {(x,y):(kind,name)}，供测试/校验用。"""
    name_r = RED_NAME
    name_b = BLK_NAME
    st = {}
    rows = fen.split(' ')[0].split('/')
    for ri, row in enumerate(rows):
        y = 9 - ri
        x = 0
        for ch in row:
            if ch.isdigit():
                x += int(ch)
            else:
                nm = name_r if ch.isupper() else name_b
                st[(x, y)] = (ch, nm[ch.upper()])
                x += 1
    return st


if __name__ == '__main__':
    import sys, json
    d = annotate(parse(sys.argv[1]))
    for m in d['moves'][:16]:
        print('%3d p=%-4d d=%-3d %s %-6s %r' % (m['i'], m['parent'], m['depth'],
              '红' if m['red'] else '黑', m['text'], m['note'][:16]))

