# -*- coding: utf-8 -*-
"""查找浏览 XQF 文件 —— 复刻 XQStudio 的「导出棋子位置 → 文件查找」功能。

· 纯逻辑: scan() 在后台线程扫描文件夹(可含子目录)内全部 XQF，
  按 文件名称/红黑方棋手/比赛结果 过滤；给定目标局面(棋子位置)时，
  重演每局的全部线路(含变着)，只列出出现过该局面的棋谱，并记录匹配步数。
· 界面: FindDialog 左侧局面预览、右侧查找条件、下方结果列表；
  单击预览匹配局面，双击在主窗口打开该棋谱并跳到匹配的那一步。
线程只做计算/写共享结果，界面由主线程 after() 轮询刷新（同云库查分）。
"""
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import xqf

RESULT_TEXT = {0: '未知', 1: '红胜', 2: '黑胜', 3: '和棋'}
RESULT_OPTS = ['不限', '红胜', '黑胜', '和棋', '未知']

RED = '#b3261e'
BLK = '#14432a'
BG = '#f6f3ea'
PANEL = '#fffdf7'
BOARD_BG = '#e9d8a6'
BOARD_LINE = '#6b5230'
CUR = '#ffd45e'
UI_FONT = 'Microsoft YaHei'
PIECE_FONT = 'KaiTi'


# ---------- 纯逻辑（可在工作线程运行，不碰 tkinter） ----------

def sig(state):
    """局面签名：只比棋子位置与种类（XQStudio「导出棋子位置」语义）。"""
    return tuple(sorted((x, y, v[0]) for (x, y), v in state.items()))


def counts(state):
    c = {}
    for v in state.values():
        c[v[0]] = c.get(v[0], 0) + 1
    return c


# 局面增量哈希表（固定种子，进程内一致）：走子/吃子只做 XOR，O(1)
import random as _random
_zrng = _random.Random(0x585146)
_Z = {(x, y, k): _zrng.getrandbits(64)
      for x in range(9) for y in range(10) for k in 'RNBAKCP' + 'rnbakcp'}


def state_hash(state):
    h = 0
    for (x, y), (kind, _name) in state.items():
        h ^= _Z[(x, y, kind)]
    return h


def match_position(d, tsig, tcounts):
    """在棋局 d 的所有线路(含变着)中找目标局面。
    返回 (着法id 或 -1=初始局面, 局面dict, 步数)；未找到返回 (None, None, 0)。
    三级筛选：子力数量(只减不增) → 增量哈希 → 完整签名复核。
    走子/撤销式 DFS，每步 O(1)，不复制整盘。"""
    init = d['init_board']
    ic = counts(init)
    for k, v in tcounts.items():
        if ic.get(k, 0) < v:
            return None, None, 0
    thash = 0
    for x, y, kind in tsig:
        thash ^= _Z[(x, y, kind)]
    ih = state_hash(init)
    if ic == tcounts and ih == thash and sig(init) == tsig:
        return -1, dict(init), 0
    kids = {}
    for m in d['moves']:
        kids.setdefault(m['parent'], []).append(m)
    board = dict(init)
    cnt = dict(ic)
    h = ih
    # 栈帧: (子着法列表, 下一个索引, 撤销信息, 深度)
    stack = [(kids.get(-1, []), 0, None, 0)]
    while stack:
        children, ci, undo, depth = stack[-1]
        if ci >= len(children):
            if undo is not None:
                fx, fy, tx, ty, piece, victim = undo
                board[(fx, fy)] = piece
                if victim is not None:
                    board[(tx, ty)] = victim
                    cnt[victim[0]] = cnt.get(victim[0], 0) + 1
                    h ^= _Z[(tx, ty, victim[0])]
                else:
                    board.pop((tx, ty), None)
                h ^= _Z[(fx, fy, piece[0])] ^ _Z[(tx, ty, piece[0])]
            stack.pop()
            continue
        m = children[ci]
        stack[-1] = (children, ci + 1, undo, depth)
        fx, fy = m['from']
        tx, ty = m['to']
        piece = board.get((fx, fy))
        if piece is None:
            # 源格无子（记录中的空着/异常着）：跳过应用但继续探索其子树，
            # 与 board_at 的宽容重演语义一致
            stack.append((kids.get(m['i'], []), 0, None, depth + 1))
            continue
        victim = board.get((tx, ty))
        board[(tx, ty)] = board.pop((fx, fy))
        if victim is not None:
            cnt[victim[0]] -= 1
            if cnt[victim[0]] == 0:
                del cnt[victim[0]]
            h ^= _Z[(tx, ty, victim[0])]
        h ^= _Z[(fx, fy, piece[0])] ^ _Z[(tx, ty, piece[0])]
        nd = depth + 1
        if cnt == tcounts and h == thash and sig(board) == tsig:
            return m['i'], dict(board), nd
        stack.append((kids.get(m['i'], []), 0, (fx, fy, tx, ty, piece, victim), nd))
    return None, None, 0


def list_xqf(folder, recursive):
    files = []
    if recursive:
        for root, _dirs, names in os.walk(folder):
            for n in names:
                if n.lower().endswith('.xqf'):
                    files.append(os.path.join(root, n))
    else:
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        for n in names:
            if n.lower().endswith('.xqf'):
                files.append(os.path.join(folder, n))
    files.sort()
    return files


def scan(folder, recursive=True, target=None, fname='', red='', black='',
         result=None, on_result=None, on_progress=None, should_stop=None):
    """扫描文件夹。target=目标局面dict(可选)；result=None 不限，否则 0..3。
    on_result(rec) 每命中一条调用一次；on_progress(done,total,matched,errors)
    每文件调用一次；should_stop() 返回 True 时提前结束。返回统计 dict。"""
    files = list_xqf(folder, recursive)
    total = len(files)
    tsig = tcounts = None
    if target is not None:
        tsig, tcounts = sig(target), counts(target)
    fname, red, black = fname.strip().lower(), red.strip().lower(), black.strip().lower()
    matched = errors = done = 0
    for p in files:
        if should_stop and should_stop():
            break
        done += 1
        try:
            d = xqf.parse(p)
        except Exception:
            errors += 1
            if on_progress:
                on_progress(done, total, matched, errors)
            continue
        info = d['info']
        name = os.path.basename(p)
        title = info.get('title') or ''
        if fname and fname not in name.lower() and fname not in title.lower():
            if on_progress:
                on_progress(done, total, matched, errors)
            continue
        if red and red not in (info.get('red') or '').lower():
            if on_progress:
                on_progress(done, total, matched, errors)
            continue
        if black and black not in (info.get('black') or '').lower():
            if on_progress:
                on_progress(done, total, matched, errors)
            continue
        if result is not None and info.get('result', 0) != result:
            if on_progress:
                on_progress(done, total, matched, errors)
            continue
        if tsig is not None:
            mi, st, depth = match_position(d, tsig, tcounts)
            if mi is None:
                if on_progress:
                    on_progress(done, total, matched, errors)
                continue
        else:
            mi, st, depth = -1, d['init_board'], 0
        matched += 1
        if on_result:
            on_result({'path': p, 'name': name, 'title': title,
                       'red': info.get('red') or '', 'black': info.get('black') or '',
                       'result': info.get('result', 0), 'move': mi, 'depth': depth,
                       'state': st, 'folder': os.path.dirname(p)})
        if on_progress:
            on_progress(done, total, matched, errors)
    return {'total': total, 'done': done, 'matched': matched, 'errors': errors}


# ---------- 局面预览小棋盘 ----------

def draw_mini(canvas, state):
    c = canvas
    c.delete('all')
    cw, ch = c.winfo_width(), c.winfo_height()
    if cw < 40 or ch < 40:
        cw, ch = 300, 340
    cell = min((cw - 24) / 8.0, (ch - 24) / 9.0)
    m = cell * 0.6
    W, H = m * 2 + cell * 8, m * 2 + cell * 9
    ox, oy = (cw - W) / 2, (ch - H) / 2
    X = lambda x: ox + m + x * cell
    Y = lambda y: oy + m + (9 - y) * cell
    c.create_rectangle(ox, oy, ox + W, oy + H, fill=BOARD_BG, outline='')
    for y in range(10):
        c.create_line(X(0), Y(y), X(8), Y(y), fill=BOARD_LINE, width=1)
    for x in range(9):
        if x in (0, 8):
            c.create_line(X(x), Y(0), X(x), Y(9), fill=BOARD_LINE, width=1)
        else:
            c.create_line(X(x), Y(0), X(x), Y(4), fill=BOARD_LINE, width=1)
            c.create_line(X(x), Y(5), X(x), Y(9), fill=BOARD_LINE, width=1)
    c.create_rectangle(X(0), Y(0), X(8), Y(9), outline=BOARD_LINE, width=2)
    for px, py in [(3, 0), (5, 0), (3, 9), (5, 9)]:
        c.create_line(X(px), Y(py), X(4), Y(py + (-1 if py else 1)), fill=BOARD_LINE, width=1)
    c.create_text(X(2), Y(4.5), text='楚 河', fill='#7a6a4f', font=(PIECE_FONT, int(cell * 0.42)))
    c.create_text(X(5.2), Y(4.5), text='汉 界', fill='#7a6a4f', font=(PIECE_FONT, int(cell * 0.42)))
    r = cell * 0.44
    for (x, y), (kind, name) in state.items():
        px, py = X(x), Y(y)
        red = kind.isupper()
        c.create_oval(px - r, py - r, px + r, py + r, fill='#f7ecd2',
                      outline=RED if red else BLK, width=1.6)
        c.create_text(px, py, text=name, fill=RED if red else BLK,
                      font=(PIECE_FONT, int(cell * 0.52), 'bold'))


# ---------- 查找浏览对话框 ----------

class FindDialog(tk.Toplevel):
    def __init__(self, app, target=None):
        super().__init__(app.root)
        self.app = app
        self.target = target
        self.results = []
        self._scan_new = []
        self._scan_prog = (0, 0, 0, 0)
        self._scan_done = True
        self._scan_stop = False
        self._scan_token = 0
        self.title('查找浏览 XQF 文件')
        self.geometry('1020x660')
        self.configure(bg=BG)
        self.transient(app.root)
        self._build_ui()
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        if target is not None:
            self._show_target()
            self.start_scan()
        else:
            self._preview_idle()

    # ---- UI ----
    def _build_ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill='x', padx=8, pady=(8, 4))
        tk.Label(top, text='文件夹:', bg=BG, font=(UI_FONT, 10)).pack(side='left')
        default = getattr(self.app, '_find_folder', None)
        if not default and getattr(self.app, 'path', None):
            default = os.path.dirname(self.app.path)
        if not default and os.path.isdir('F:/XQ/xq'):
            default = 'F:/XQ/xq'
        self.folder_var = tk.StringVar(value=default or '')
        tk.Entry(top, textvariable=self.folder_var, font=(UI_FONT, 10)).pack(
            side='left', fill='x', expand=True, padx=6)
        tk.Button(top, text='浏览…', command=self._browse, font=(UI_FONT, 9),
                  bg='#54493a', fg='#f7f1e3', relief='flat', padx=8).pack(side='left')
        self.rec_var = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text='包含子目录', variable=self.rec_var, bg=BG,
                       font=(UI_FONT, 10)).pack(side='left', padx=8)

        mid = tk.Frame(self, bg=BG)
        mid.pack(fill='x', padx=8, pady=4)
        # 左：局面预览
        pf = tk.LabelFrame(mid, text='局面预览', bg=PANEL, fg='#7a6a4f',
                           font=(UI_FONT, 9, 'bold'))
        pf.pack(side='left', fill='y')
        self.preview = tk.Canvas(pf, bg=BG, highlightthickness=0, width=280, height=300)
        self.preview.pack(padx=6, pady=(6, 2))
        self.preview_lbl = tk.Label(pf, text='没有指定XQF文件', bg=PANEL, fg='#7a6a4f',
                                    font=(UI_FONT, 9), wraplength=270, justify='left')
        self.preview_lbl.pack(fill='x', padx=6, pady=(0, 6))
        self.preview.bind('<Configure>', lambda e: self._redraw_preview())
        # 右：查找条件
        rf = tk.LabelFrame(mid, text='查找条件', bg=PANEL, fg='#7a6a4f',
                           font=(UI_FONT, 9, 'bold'))
        rf.pack(side='left', fill='both', expand=True, padx=(8, 0))
        self.fname_var = tk.StringVar()
        self.red_var = tk.StringVar()
        self.black_var = tk.StringVar()
        self.result_var = tk.StringVar(value='不限')
        for row, (lab, var) in enumerate([('文件名称:', self.fname_var),
                                          ('红方棋手:', self.red_var),
                                          ('黑方棋手:', self.black_var)]):
            tk.Label(rf, text=lab, bg=PANEL, font=(UI_FONT, 10), anchor='e').grid(
                row=row, column=0, sticky='e', padx=(10, 4), pady=4)
            tk.Entry(rf, textvariable=var, font=(UI_FONT, 10), width=26).grid(
                row=row, column=1, sticky='w', pady=4)
        tk.Label(rf, text='比赛结果:', bg=PANEL, font=(UI_FONT, 10), anchor='e').grid(
            row=3, column=0, sticky='e', padx=(10, 4), pady=4)
        ttk.Combobox(rf, textvariable=self.result_var, values=RESULT_OPTS, width=23,
                     state='readonly', font=(UI_FONT, 10)).grid(row=3, column=1, sticky='w', pady=4)
        bf = tk.Frame(rf, bg=PANEL)
        bf.grid(row=4, column=0, columnspan=2, sticky='w', padx=10, pady=(8, 10))
        self.find_btn = tk.Button(bf, text='查找', command=self.start_scan, font=(UI_FONT, 10, 'bold'),
                                  bg='#54493a', fg='#f7f1e3', relief='flat', padx=14)
        self.find_btn.pack(side='left')
        self.stop_btn = tk.Button(bf, text='停止', command=self.stop_scan, font=(UI_FONT, 10),
                                  bg='#7a3b2e', fg='#f7f1e3', relief='flat', padx=10, state='disabled')
        self.stop_btn.pack(side='left', padx=6)
        tk.Button(bf, text='使用当前局面', command=self.use_current, font=(UI_FONT, 10),
                  bg='#54493a', fg='#f7f1e3', relief='flat', padx=10).pack(side='left', padx=6)
        tk.Button(bf, text='清除局面条件', command=self.clear_target, font=(UI_FONT, 10),
                  bg='#54493a', fg='#f7f1e3', relief='flat', padx=10).pack(side='left')
        self.target_lbl = tk.Label(rf, text='局面条件：无（按条件列出全部棋谱）', bg=PANEL,
                                   fg='#9a8a6b', font=(UI_FONT, 9), anchor='w')
        self.target_lbl.grid(row=5, column=0, columnspan=2, sticky='w', padx=10, pady=(0, 8))
        rf.columnconfigure(1, weight=1)

        # 下：结果列表（状态栏先 pack，避免被 expand 的列表挤没）
        self.status = tk.Label(self, text='就绪', bg='#e7dfc9', fg='#5a4f3a',
                               font=(UI_FONT, 9), anchor='w')
        self.status.pack(fill='x', side='bottom')
        lf = tk.Frame(self, bg=BG)
        lf.pack(fill='both', expand=True, padx=8, pady=4)
        style = ttk.Style(self)
        style.configure('Find.Treeview', font=(UI_FONT, 9), rowheight=24)
        style.configure('Find.Treeview.Heading', font=(UI_FONT, 9, 'bold'))
        cols = ('name', 'red', 'black', 'result', 'depth', 'folder')
        self.tv = ttk.Treeview(lf, columns=cols, show='headings', selectmode='browse',
                               style='Find.Treeview')
        for cid, txt, w in [('name', '棋局文件', 280), ('red', '红方', 90), ('black', '黑方', 90),
                            ('result', '结果', 60), ('depth', '匹配步', 64), ('folder', '所在文件夹', 320)]:
            self.tv.heading(cid, text=txt)
            self.tv.column(cid, width=w, stretch=(cid in ('name', 'folder')))
        vsb = ttk.Scrollbar(lf, orient='vertical', command=self.tv.yview)
        self.tv.configure(yscrollcommand=vsb.set)
        self.tv.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self.tv.bind('<<TreeviewSelect>>', self._on_select)
        self.tv.bind('<Double-Button-1>', self._on_dclick)

    # ---- 预览 ----
    def _preview_state(self):
        sel = self.tv.selection()
        if sel:
            k = int(sel[0])
            if 0 <= k < len(self.results):
                return self.results[k]['state']
        return self.target

    def _redraw_preview(self):
        st = self._preview_state()
        if st is not None:
            draw_mini(self.preview, st)
        else:
            self.preview.delete('all')

    def _preview_idle(self):
        self.preview.delete('all')
        self.preview_lbl.config(text='没有指定XQF文件')

    def _show_target(self):
        draw_mini(self.preview, self.target)
        self.preview_lbl.config(text='当前局面（已导出棋子位置）')
        self.target_lbl.config(text='局面条件：已导出当前棋子位置，只查找出现过该局面的棋谱')

    # ---- 条件操作 ----
    def _browse(self):
        p = filedialog.askdirectory(parent=self, title='选择棋谱文件夹',
                                    initialdir=self.folder_var.get() or None)
        if p:
            self.folder_var.set(p)

    def use_current(self):
        if getattr(self.app, 'd', None) is None:
            messagebox.showinfo('使用当前局面', '主窗口尚未打开棋谱', parent=self)
            return
        self.set_target(self.app.board_at(self.app.cur))

    def set_target(self, state):
        self.target = state
        self._show_target()
        self.start_scan()

    def clear_target(self):
        self.target = None
        self.target_lbl.config(text='局面条件：无（按条件列出全部棋谱）')
        self._preview_idle()

    # ---- 扫描（工作线程 + 主线程轮询） ----
    def start_scan(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror('查找', '请先选择有效的棋谱文件夹', parent=self)
            return
        self.app._find_folder = folder
        res_txt = self.result_var.get()
        result = {'红胜': 1, '黑胜': 2, '和棋': 3, '未知': 0}.get(res_txt)
        self.tv.delete(*self.tv.get_children())
        self.results = []
        self._scan_new = []
        self._scan_prog = (0, 0, 0, 0)
        self._scan_done = False
        self._scan_stop = False
        self._scan_token += 1
        tok = self._scan_token
        self.find_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.status.config(text='正在扫描 %s …' % folder)
        args = dict(folder=folder, recursive=self.rec_var.get(), target=self.target,
                    fname=self.fname_var.get(), red=self.red_var.get(),
                    black=self.black_var.get(), result=result)
        threading.Thread(target=self._worker, args=(args, tok), daemon=True).start()
        self.after(120, lambda: self._poll(tok))

    def _worker(self, args, tok):
        def on_result(rec):
            if tok == self._scan_token:
                self._scan_new.append(rec)

        def on_progress(done, total, matched, errors):
            if tok == self._scan_token:
                self._scan_prog = (done, total, matched, errors)

        try:
            stat = scan(should_stop=lambda: self._scan_stop,
                        on_result=on_result, on_progress=on_progress, **args)
        except Exception as e:
            stat = {'total': 0, 'done': 0, 'matched': 0, 'errors': 0, 'err': str(e)}
        if tok == self._scan_token:
            self._scan_stat = stat
            self._scan_done = True

    def _drain(self):
        batch, self._scan_new = self._scan_new, []
        for rec in batch:
            k = len(self.results)
            self.results.append(rec)
            label = rec['title'] or rec['name']
            depth = '初始' if rec['move'] < 0 else str(rec['depth'])
            self.tv.insert('', 'end', iid=str(k), values=(
                label, rec['red'], rec['black'], RESULT_TEXT.get(rec['result'], '?'),
                depth, rec['folder']))

    def _poll(self, tok):
        if tok != self._scan_token or not self.winfo_exists():
            return
        self._drain()
        done, total, matched, errors = self._scan_prog
        if not self._scan_done:
            self.status.config(text='文件个数: %d　已扫描: %d　匹配: %d%s' % (
                total, done, matched, ('　损坏: %d' % errors) if errors else ''))
            self.after(120, lambda: self._poll(tok))
            return
        self._drain()  # 收尾再取一次，防止最后一条结果落在 drain 与置 done 之间
        stat = getattr(self, '_scan_stat', {})
        msg = '扫描完成：文件 %d 个，匹配 %d 个' % (stat.get('done', 0), stat.get('matched', 0))
        if stat.get('errors'):
            msg += '，损坏/无法解析 %d 个' % stat['errors']
        if stat.get('err'):
            msg += '（出错: %s）' % stat['err']
        if self._scan_stop:
            msg = '已停止。' + msg
        self.status.config(text=msg)
        self.find_btn.config(state='normal')
        self.stop_btn.config(state='disabled')

    def stop_scan(self):
        self._scan_stop = True

    # ---- 结果交互 ----
    def _on_select(self, _e):
        sel = self.tv.selection()
        if not sel:
            return
        k = int(sel[0])
        if not (0 <= k < len(self.results)):
            return
        rec = self.results[k]
        draw_mini(self.preview, rec['state'])
        step = '初始局面' if rec['move'] < 0 else '第 %d 着' % rec['depth']
        self.preview_lbl.config(text='%s\n%s' % (rec['name'], step))

    def _on_dclick(self, _e):
        sel = self.tv.selection()
        if not sel:
            return
        k = int(sel[0])
        if not (0 <= k < len(self.results)):
            return
        rec = self.results[k]
        app = self.app
        app.load(rec['path'])
        if app.d is None:
            return
        if rec['move'] >= 0 and rec['move'] in app.by_id:
            # 让显示线路经过匹配步：沿父链设置 choice，再跳转
            chain = []
            mi = rec['move']
            while mi >= 0:
                chain.append(mi)
                mi = app.by_id[mi]['parent']
            for mi in reversed(chain):
                app.choice[app.by_id[mi]['parent']] = mi
            app.rebuild_line()
            app.go(rec['move'])
        else:
            app.go(-1)
        app.root.lift()
        app.set_status('已从查找窗口打开 %s（第 %d 着匹配当前局面）' % (
            rec['name'], rec['depth']))

    def _on_close(self):
        self._scan_stop = True
        self._scan_token += 1
        if getattr(self.app, '_find_dlg', None) is self:
            self.app._find_dlg = None
        self.destroy()