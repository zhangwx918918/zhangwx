# -*- coding: utf-8 -*-
"""XQF 写入器：镜像 xqf.py 读取器的加密与着法树序列化。

策略：编辑已有 XQF 时，原样保留 1024 字节棋头（初始局面/信息/密钥不变），
只重新编码走子+注解体；新建文件时构造标准开局棋头。
回环正确性由 roundtrip 测试保证（parse -> write -> parse 比对）。
"""
import struct
import xqf

HEADER_SIZE = 1024


def _keys_from_header(data):
    version = data[2]
    (mask, _pid, oa, ob, oc, od, ksum, kxy_raw, kxyf_raw, kxyt_raw) = \
        struct.unpack('<BIBBBBBBBB', data[3:16])
    if version >= 11:
        return xqf._key_derive(kxy_raw, kxyf_raw, kxyt_raw, ksum, mask, (oa, ob, oc, od))
    return (None, None, None, None, None)


def _encode_stream(keys32, buf):
    out = bytearray(buf)
    for i in range(len(out)):
        out[i] = (out[i] + keys32[i % 32]) & 0xFF
    return bytes(out)


def _encode_board(board32, k_xy):
    """board32: 32 长度, index->位置(x*10+y) 或 0xFF。返回 man_raw(32)."""
    if k_xy is None:
        return bytes(board32)
    tmp = [0] * 32
    for i in range(32):
        v = board32[i]
        tmp[i] = 0xFF if v == 0xFF else (v + k_xy) & 0xFF
    man = [0] * 32
    for i in range(32):
        man[i] = tmp[(k_xy + i + 1) & 0x1F]
    return bytes(man)


def serialize_tree(children, roots):
    """按读取器的状态机生成走子流顺序与标志。返回 [(idx, has_next, has_var)]."""
    order = []
    pending = []                      # (parent, next_child_pos)
    if not roots:
        return order
    cur = roots[0]
    cur_parent, cur_pos = -1, 0
    while cur is not None:
        ch = children.get(cur, [])
        has_next = bool(ch)
        sib = children.get(cur_parent, [])
        has_var = (cur_pos + 1 < len(sib))
        order.append((cur, has_next, has_var))
        if has_var:
            pending.append((cur_parent, cur_pos + 1))
        if has_next:
            cur_parent, cur_pos, cur = cur, 0, ch[0]
        elif pending:
            cur_parent, cur_pos = pending.pop()
            cur = children[cur_parent][cur_pos]
        else:
            cur = None
    return order


def _build_header(info, board32):
    """新建文件：构造 version 18 棋头（默认密钥 + 给定初始局面）。"""
    version = 18
    # 默认密钥原料（取一组固定值，保证可自洽加解密）
    mask, pid = 0xFF, 0x12345678
    oa = ob = oc = od = 0xFF
    ksum, kxy_raw, kxyf_raw, kxyt_raw = 0x12, 0x34, 0x56, 0x78
    head = bytearray(HEADER_SIZE)
    head[0:2] = b'XQ'
    head[2] = version
    head[3:16] = struct.pack('<BIBBBBBBBB', mask, pid, oa, ob, oc, od,
                             ksum, kxy_raw, kxyf_raw, kxyt_raw)
    k_xy, k_xyf, k_xyt, k_rmk, keys32 = xqf._key_derive(
        kxy_raw, kxyf_raw, kxyt_raw, ksum, mask, (oa, ob, oc, od))
    head[16:48] = _encode_board(board32, k_xy)
    head[51] = info.get('result', 0)
    head[64] = info.get('type', 1)

    def put(stroff, lenoff, s, cap):
        b = s.encode('gb18030', 'ignore')[:cap]
        head[lenoff] = len(b)
        head[stroff:stroff + len(b)] = b
    put(81, 80, info.get('title', ''), 63)
    put(209, 208, info.get('event', ''), 63)
    put(273, 272, info.get('date', ''), 15)
    put(289, 288, info.get('place', ''), 15)
    put(305, 304, info.get('red', ''), 15)
    put(321, 320, info.get('black', ''), 15)
    put(337, 336, info.get('rule', ''), 63)
    put(401, 400, info.get('red_time', ''), 15)
    put(417, 416, info.get('black_time', ''), 15)
    return bytes(head)


def standard_board32():
    """标准开局 32 子位置数组（按 PIECE_KIND 顺序, 红0..15 黑16..31）。"""
    # 红方底线 y=0: 车马相仕帅仕相马车 (x0..8), 炮 x1,x7 y2, 兵 x0,2,4,6,8 y3
    red = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0), (8, 0),
           (1, 2), (7, 2), (0, 3), (2, 3), (4, 3), (6, 3), (8, 3)]
    # 黑方 y=9 底线, 炮 y7, 卒 y6
    blk = [(0, 9), (1, 9), (2, 9), (3, 9), (4, 9), (5, 9), (6, 9), (7, 9), (8, 9),
           (1, 7), (7, 7), (0, 6), (2, 6), (4, 6), (6, 6), (8, 6)]
    arr = [0xFF] * 32
    for mi, (x, y) in enumerate(red):
        arr[mi] = x * 10 + y
    for mi, (x, y) in enumerate(blk):
        arr[16 + mi] = x * 10 + y
    return arr


def merge(d, other_path):
    """真正合并：把另一 XQF 的着法树 graft 进当前树。

    从根开始逐着比对——相同着法(同起点同终点)共用已有节点继续往下合并，
    只有着法不同处才把对方的分支整段接为新变着。局面相同的前缀自动合为一谱，
    与 XQStudio 的合并语义一致（而非把每局简单罗列成根线路）。
    返回新增着法数。"""
    o = xqf.parse(other_path)
    if not o['moves']:
        return 0
    o_by = {m['i']: m for m in o['moves']}
    o_ch = {}
    for m in o['moves']:
        o_ch.setdefault(m['parent'], []).append(m['i'])

    d_by = {m['i']: m for m in d['moves']}
    d_ch = {}
    for m in d['moves']:
        d_ch.setdefault(m['parent'], []).append(m['i'])
    next_i = max(d_by, default=-1) + 1
    added = 0

    def key(m):
        return (tuple(m['from']), tuple(m['to']))

    def copy_subtree(o_node, d_parent):
        nonlocal next_i, added
        om = o_by[o_node]
        ni = next_i; next_i += 1
        nm = {'i': ni, 'parent': d_parent,
              'from': list(om['from']), 'to': list(om['to']),
              'piece': om['piece'], 'note': om.get('note', '')}
        d['moves'].append(nm)
        d_by[ni] = nm
        d_ch.setdefault(d_parent, []).append(ni)
        added += 1
        for oc in o_ch.get(o_node, []):
            copy_subtree(oc, ni)
        return ni

    def graft(d_parent, o_parent):
        for oc in o_ch.get(o_parent, []):
            om = o_by[oc]
            k = key(om)
            match = None
            for ec in d_ch.get(d_parent, []):
                if key(d_by[ec]) == k:
                    match = ec; break
            if match is not None:
                em = d_by[match]
                if not em.get('note') and om.get('note'):
                    em['note'] = om['note']
                graft(match, oc)        # 前缀相同，继续往下合并
            else:
                copy_subtree(oc, d_parent)   # 着法不同，接为新变着

    graft(-1, -1)
    return added


def write(d, out_path, original_path=None):
    """d: xqf.parse 的结果(可经 annotate)。original_path 提供则复用其棋头。"""
    moves = d['moves']
    children = {}
    for m in moves:
        children.setdefault(m['parent'], []).append(m['i'])
    roots = children.get(-1, [])
    order = serialize_tree(children, roots)

    if original_path:
        header = open(original_path, 'rb').read()[:HEADER_SIZE]
    else:
        board32 = standard_board32()
        header = _build_header(d.get('info', {}), board32)
    k_xy, k_xyf, k_xyt, k_rmk, keys32 = _keys_from_header(header)
    encrypted = keys32 is not None

    body = bytearray()
    gnote = d.get('game_note', '') or ''
    body += bytes([0, 0, 0x20 if gnote else 0x00, 0])
    if gnote:
        gb = gnote.encode('gb18030', 'ignore')
        n = len(gb) + (k_rmk if encrypted else 0)
        body += struct.pack('<I', n) + gb

    by_idx = {m['i']: m for m in moves}
    for idx, has_next, has_var in order:
        m = by_idx[idx]
        fx, fy = m['from']
        tx, ty = m['to']
        frm, to = fx * 10 + fy, tx * 10 + ty
        note = m.get('note', '') or ''
        fl = (0x80 if has_next else 0) | (0x40 if has_var else 0) | (0x20 if note else 0)
        if encrypted:
            b0 = (frm + xqf.MOVE_FROM_OFFSET + k_xyf) & 0xFF
            b1 = (to + xqf.MOVE_TO_OFFSET + k_xyt) & 0xFF
        else:
            b0 = (frm + xqf.MOVE_FROM_OFFSET) & 0xFF
            b1 = (to + xqf.MOVE_TO_OFFSET) & 0xFF
        body += bytes([b0, b1, fl, 0])
        if note:
            nb = note.encode('gb18030', 'ignore')
            n = len(nb) + (k_rmk if encrypted else 0)
            body += struct.pack('<I', n) + nb

    if encrypted:
        body = _encode_stream(keys32, body)
    with open(out_path, 'wb') as f:
        f.write(header + bytes(body))
    return out_path


if __name__ == '__main__':
    import sys
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else 'roundtrip.xqf'
    d = xqf.parse(src)
    write(d, out, original_path=src)
    d2 = xqf.parse(out)
    # 比对
    ok = True
    if len(d['moves']) != len(d2['moves']):
        ok = False; print('MOVE COUNT DIFF', len(d['moves']), len(d2['moves']))
    a = {m['i']: m for m in d['moves']}
    b = {m['i']: m for m in d2['moves']}
    diffs = 0
    for i in a:
        if i not in b:
            ok = False; print('missing', i); continue
        for key in ('parent', 'from', 'to', 'note'):
            if a[i][key] != b[i][key]:
                diffs += 1
                if diffs <= 10:
                    print('DIFF i=%d %s: %r != %r' % (i, key, a[i][key], b[i][key]))
                ok = False
    if d['game_note'] != d2['game_note']:
        ok = False; print('GAME NOTE DIFF')
    if d['init_board'] != d2['init_board']:
        ok = False; print('INIT BOARD DIFF')
    print('moves=%d diffs=%d ROUNDTRIP %s' % (len(a), diffs, 'OK' if ok else 'FAIL'))