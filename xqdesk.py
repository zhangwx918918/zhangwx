# -*- coding: utf-8 -*-
"""棋谱工作台 —— 原生桌面版，参考网页版界面。
三栏(棋图/棋谱/注解)可拖动；棋谱、注解字号各自可调；棋盘可缩放；
点击棋盘输入着法/变着、编辑注解、保存 XQF。复用 xqf.py / xqf_write.py。
"""
import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import xqf
import xqf_write
import xqf_import
import xqfind

RED = '#b3261e'
BLK = '#14432a'
BG = '#f6f3ea'
PANEL = '#fffdf7'
LINE = '#c9b98f'
BOARD_BG = '#e9d8a6'
BOARD_LINE = '#6b5230'
HL = '#ffe9a8'
CUR = '#ffd45e'
UI_FONT = 'Microsoft YaHei'
PIECE_FONT = 'KaiTi'


def path_to(moves_by_id, i):
    p = []
    while i is not None and i >= 0:
        p.insert(0, i)
        i = moves_by_id[i]['parent']
    return p


class App:
    def __init__(self, root):
        self.root = root
        root.title('棋谱工作台')
        root.geometry('1180x760')
        root.configure(bg=BG)
        self.d = None
        self.path = None
        self.cur = -1
        self.dirty = False
        self.selected = None          # 输入时选中的格子 (x,y)
        self.play_timer = None
        self.choice = {}              # 每个分支点当前选定的后续着法(纯显示用)
        self.line = []                # 当前显示的线路(着法 id 列表)
        self.var_items = []           # 变着窗各行对应的着法 id
        self.fs_move = tk.IntVar(value=18)
        self.fs_note = tk.IntVar(value=16)
        self.cell = tk.IntVar(value=46)
        self._build_ui()
        self._bind_keys()
        # 默认加载示例
        demo = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'demo.xqf')
        if os.path.exists(demo):
            self.load(demo)

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('Treeview', font=(UI_FONT, self.fs_move.get()),
                        rowheight=int(self.fs_move.get() * 1.7), background=PANEL,
                        fieldbackground=PANEL, foreground='#1f2328')
        style.configure('Treeview.Heading', font=(UI_FONT, 11))
        style.map('Treeview', background=[('selected', CUR)], foreground=[('selected', '#1f2328')])
        self.style = style

        menubar = tk.Menu(self.root)
        fm = tk.Menu(menubar, tearoff=0)
        fm.add_command(label='打开 XQF…', command=self.open_file)
        fm.add_command(label='保存', command=self.save_file)
        fm.add_command(label='另存为…', command=self.save_as)
        fm.add_command(label='新建', command=self.new_file)
        fm.add_separator()
        fm.add_command(label='导入文本棋谱 (txt/chn/ccm/mxq/che)…', command=self.import_text)
        fm.add_command(label='合并其它 XQF 到本文件…', command=self.merge_xqf)
        fm.add_command(label='批量转换非 XQF 文件夹…', command=self.batch_convert_gui)
        fm.add_command(label='查找浏览 XQF 文件…\tCtrl+F', command=self.find_games)
        fm.add_separator()
        fm.add_command(label='退出', command=self.root.quit)
        menubar.add_cascade(label='文件', menu=fm)
        self.root.config(menu=menubar)

        top = tk.Frame(self.root, bg='#3c3428')
        top.pack(fill='x')
        r1 = tk.Frame(top, bg='#3c3428'); r1.pack(fill='x', padx=10, pady=(6, 2))
        tk.Label(r1, text='棋谱工作台', fg='#f7f1e3', bg='#3c3428',
                 font=(UI_FONT, 13, 'bold')).pack(side='left')
        self.title_lbl = tk.Label(r1, text='未打开', fg='#cdbf9f', bg='#3c3428', font=(UI_FONT, 10))
        self.title_lbl.pack(side='left', padx=12)
        for txt, cmd in [('打开', self.open_file), ('保存', self.save_file),
                         ('另存为', self.save_as), ('新建', self.new_file),
                         ('导入文本', self.import_text), ('合并XQF', self.merge_xqf),
                         ('批量转换', self.batch_convert_gui),
                         ('查找棋谱', self.find_games),
                         ('云库FEN', self.copy_fen), ('云库分数', self.query_cloud)]:
            tk.Button(r1, text=txt, command=cmd, font=(UI_FONT, 10),
                      bg='#54493a', fg='#f7f1e3', activebackground='#6a5c48',
                      relief='flat', padx=7).pack(side='left', padx=2)
        self.dirty_lbl = tk.Label(r1, text='', fg='#ffd45e', bg='#3c3428', font=(UI_FONT, 10))
        self.dirty_lbl.pack(side='left', padx=8)

        r2 = tk.Frame(top, bg='#3c3428'); r2.pack(fill='x', padx=10, pady=(0, 6))
        self._slider(r2, '棋谱字号', self.fs_move, 10, 40, self.on_fs_move)
        self._slider(r2, '注解字号', self.fs_note, 10, 40, self.on_fs_note)
        self._slider(r2, '棋盘', self.cell, 26, 80, lambda e: self.draw_board())
        tk.Label(r2, text='  播放:', fg='#f7f1e3', bg='#3c3428', font=(UI_FONT, 10)).pack(side='left')
        for txt, cmd in [('|◀', self.go_first), ('◀', lambda: self.step(-1)),
                         ('▶ 自动', self.toggle_play), ('▶', lambda: self.step(1)),
                         ('▶|', self.go_last)]:
            tk.Button(r2, text=txt, command=cmd, font=(UI_FONT, 10), bg='#54493a',
                      fg='#f7f1e3', activebackground='#6a5c48', relief='flat', padx=8).pack(side='left', padx=2)
        tk.Button(r2, text='删除本步', command=self.delete_move, font=(UI_FONT, 10),
                  bg='#7a3b2e', fg='#f7f1e3', relief='flat', padx=8).pack(side='left', padx=(12, 2))
        tk.Label(r2, text='(点击棋盘走子输入)', fg='#cdbf9f', bg='#3c3428',
                 font=(UI_FONT, 9)).pack(side='left', padx=8)

        # 三栏
        self.pw = tk.PanedWindow(self.root, orient='horizontal', sashwidth=7,
                                 bg='#d9cba4', sashrelief='raised', bd=0)
        self.pw.pack(fill='both', expand=True)

        # 棋图栏
        f_board = tk.Frame(self.pw, bg=BG)
        self.board_canvas = tk.Canvas(f_board, bg=BG, highlightthickness=0)
        self.board_canvas.pack(fill='both', expand=True, padx=8, pady=8)
        self.board_canvas.bind('<Button-1>', self.on_board_click)
        self.board_canvas.bind('<Button-3>', self.on_board_right_click)
        self.board_canvas.bind('<Configure>', lambda e: self.draw_board())
        self.cur_line = tk.Label(f_board, text='初始局面', bg=PANEL, fg='#1f2328',
                                 font=(UI_FONT, 11, 'bold'), anchor='w')
        self.cur_line.pack(fill='x', padx=8, pady=(0, 8))
        self.pw.add(f_board, minsize=240, width=430)

        # 棋谱栏
        f_moves = tk.Frame(self.pw, bg=PANEL)
        tk.Label(f_moves, text='棋谱（点击跳转；m=有变着  *=有注解）', bg=PANEL, fg='#7a6a4f',
                 font=(UI_FONT, 10, 'bold'), anchor='w').pack(fill='x', padx=6, pady=4)
        self.tree = ttk.Treeview(f_moves, columns=('mv',), show='tree', selectmode='browse')
        self.tree.heading('#0', text='着法')
        self.tree.column('#0', width=300, stretch=True)
        self.tree.tag_configure('red', foreground=RED)
        self.tree.tag_configure('blk', foreground=BLK)
        self.tree.tag_configure('var', foreground='#8a7a5c')
        vsb = ttk.Scrollbar(f_moves, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side='left', fill='both', expand=True, padx=(6, 0), pady=(0, 6))
        vsb.pack(side='right', fill='y', pady=(0, 6))
        self.tree.bind('<<TreeviewSelect>>', self.on_tree_select)
        self.pw.add(f_moves, minsize=200, width=360)

        # 注解栏
        f_note = tk.Frame(self.pw, bg=PANEL)
        nh = tk.Frame(f_note, bg=PANEL); nh.pack(fill='x', padx=6, pady=4)
        tk.Label(nh, text='注解（可编辑）', bg=PANEL, fg='#7a6a4f',
                 font=(UI_FONT, 10, 'bold')).pack(side='left')
        tk.Button(nh, text='写入本步', command=self.commit_note, font=(UI_FONT, 9),
                  bg='#54493a', fg='#f7f1e3', relief='flat', padx=8).pack(side='right')
        self.note_head = tk.Label(f_note, text='', bg=PANEL, fg='#9a8a6b',
                                  font=(UI_FONT, 9), anchor='w')
        self.note_head.pack(fill='x', padx=6)
        # 注解 / 变着 上下分割：中间分割线可上下拖动，变着多时拉大下窗即可看全
        self.note_paned = tk.PanedWindow(f_note, orient='vertical', sashwidth=7,
                                         sashrelief='raised', bg='#d9cba4', bd=0,
                                         showhandle=True, handlesize=8)
        self.note_paned.pack(fill='both', expand=True, padx=6, pady=(2, 6))
        # 上：注解编辑
        note_fr = tk.Frame(self.note_paned, bg=PANEL)
        self.note_txt = tk.Text(note_fr, wrap='word', bg='#f3eee0', fg='#4a4438',
                                font=(UI_FONT, self.fs_note.get()), relief='flat',
                                padx=10, pady=8, undo=True, height=10)
        nsb = ttk.Scrollbar(note_fr, orient='vertical', command=self.note_txt.yview)
        self.note_txt.configure(yscrollcommand=nsb.set)
        self.note_txt.pack(side='left', fill='both', expand=True)
        nsb.pack(side='right', fill='y')
        self.note_txt.bind('<FocusOut>', lambda e: self.commit_note(silent=True))
        self.note_paned.add(note_fr, minsize=70, stretch='always')
        # 下：本步的所有走法（变着选择窗，双击切换线路；带滚动条）
        var_fr = tk.LabelFrame(self.note_paned, text='本步的所有走法（双击切换变着）', bg=PANEL,
                               fg='#7a6a4f', font=(UI_FONT, 9, 'bold'))
        self.varlist = tk.Listbox(var_fr, height=6, font=(UI_FONT, 12), activestyle='none',
                                  bg='#f3eee0', fg='#4a4438', selectbackground=CUR,
                                  selectforeground='#1f2328', relief='flat', highlightthickness=0)
        vsb = ttk.Scrollbar(var_fr, orient='vertical', command=self.varlist.yview)
        self.varlist.configure(yscrollcommand=vsb.set)
        self.varlist.pack(side='left', fill='both', expand=True, padx=(4, 0), pady=4)
        vsb.pack(side='right', fill='y', pady=4)
        self.varlist.bind('<Double-Button-1>', self.on_var_dclick)
        self.note_paned.add(var_fr, minsize=60, stretch='always')
        self.pw.add(f_note, minsize=200, width=360)
        self.root.after(150, self._init_note_sash)

        self.status = tk.Label(self.root, text='就绪', bg='#e7dfc9', fg='#5a4f3a',
                               font=(UI_FONT, 9), anchor='w')
        self.status.pack(fill='x')

    def _init_note_sash(self):
        try:
            h = self.note_paned.winfo_height()
            if h > 60:
                self.note_paned.sash_place(0, 0, int(h * 0.62))
        except Exception:
            pass

    def _slider(self, parent, label, var, lo, hi, cmd):
        tk.Label(parent, text=label, fg='#f7f1e3', bg='#3c3428',
                 font=(UI_FONT, 10)).pack(side='left', padx=(8, 2))
        s = tk.Scale(parent, from_=lo, to=hi, orient='horizontal', variable=var,
                     command=cmd, bg='#3c3428', fg='#f7f1e3', troughcolor='#6b5f4b',
                     highlightthickness=0, length=110, showvalue=True, font=(UI_FONT, 8))
        s.pack(side='left')

    def _bind_keys(self):
        self.root.bind('<Left>', lambda e: self.step(-1))
        self.root.bind('<Right>', lambda e: self.step(1))
        self.root.bind('<space>', lambda e: self.step(1))
        self.root.bind('<Home>', lambda e: self.go_first())
        self.root.bind('<End>', lambda e: self.go_last())
        self.root.bind('<Control-s>', lambda e: self.save_file())
        self.root.bind('<Control-o>', lambda e: self.open_file())
        self.root.bind('<Control-f>', lambda e: self.find_games())

    # ---------- 数据 ----------
    def load(self, path):
        try:
            d = xqf.annotate(xqf.parse(path))
        except Exception as e:
            messagebox.showerror('打开失败', str(e)); return
        self.commit_note(silent=True)
        self.d = d
        self.path = path
        self.by_id = {m['i']: m for m in d['moves']}
        self.children = {}
        for m in d['moves']:
            self.children.setdefault(m['parent'], []).append(m['i'])
        self.cur = -1
        self.selected = None
        self.choice = {}
        self.dirty = False
        self.title_lbl.config(text='%s（%d 着）' % (d['info'].get('title') or os.path.basename(path), len(d['moves'])))
        self.rebuild_line()
        self.go(-1)
        self.set_status('已打开 %s' % path)

    def board_at(self, i):
        st = dict(self.d['init_board'])
        for mi in path_to(self.by_id, i):
            m = self.by_id[mi]
            fx, fy = m['from']; tx, ty = m['to']
            if (fx, fy) in st:
                st[(tx, ty)] = st.pop((fx, fy))
        return st

    def build_line(self):
        # 沿每个分支点选定的后续着法(choice，默认首个=主线)走出一条完整线路
        roots = self.children.get(-1, [])
        self.line = []
        if not roots:
            return
        r = self.choice.get(-1, roots[0])
        if r not in roots:
            r = roots[0]
        idx = r
        seen = set()
        while idx is not None and idx >= 0 and idx not in seen:
            self.line.append(idx)
            seen.add(idx)
            kids = self.children.get(idx, [])
            if not kids:
                break
            nxt = self.choice.get(idx, kids[0])
            if nxt not in kids:
                nxt = kids[0]
            idx = nxt

    def rebuild_line(self):
        # 棋谱区只平铺当前线路；m=该着有变着分支，*=该着有注解
        self.build_line()
        self.tree.delete(*self.tree.get_children())
        for idx in self.line:
            m = self.by_id[idx]
            n = len(path_to(self.by_id, idx))
            no = (n + 1) // 2
            tag = 'red' if m.get('red') else 'blk'
            # m=该着有同级变着(此位置有多个候选走法，可在变着窗切换)；*=该着有注解
            has_var = len(self.children.get(m['parent'], [])) > 1
            suffix = ('m' if has_var else '') + ('*' if m.get('note') else '')
            label = '%d%s %s%s' % (no, '.' if m.get('red') else '..', m.get('text', '?'), suffix)
            self.tree.insert('', 'end', iid=str(idx), text=label, tags=(tag,), open=True)
        if self.cur >= 0 and self.tree.exists(str(self.cur)):
            self.tree.selection_set(str(self.cur))
            self.tree.see(str(self.cur))

    # ---------- 变着选择窗 ----------
    def refresh_var_panel(self):
        self.varlist.delete(0, 'end')
        self.var_items = []
        if self.d is None:
            return
        p = self.by_id[self.cur]['parent'] if self.cur >= 0 else -1
        sibs = self.children.get(p, [])
        if not sibs:
            return
        active = self.choice.get(p, sibs[0])
        if active not in sibs:
            active = sibs[0]
        for k, s in enumerate(sibs):
            letter = chr(ord('A') + k) if k < 26 else str(k + 1)
            txt = self.by_id[s].get('text', '?')
            mark = '  ←当前' if s == self.cur else ('  (主)' if s == active else '')
            self.varlist.insert('end', '%s. %s%s' % (letter, txt, mark))
            self.var_items.append(s)
        if active in self.var_items:
            k = self.var_items.index(active)
            self.varlist.selection_set(k)
            self.varlist.see(k)

    def on_var_dclick(self, e):
        sel = self.varlist.curselection()
        if not sel or not self.var_items:
            return
        k = sel[0]
        if k >= len(self.var_items):
            return
        s = self.var_items[k]
        p = self.by_id[s]['parent']
        self.choice[p] = s
        self.rebuild_line()
        self.go(s)
        self.set_status('已切换到变着：%s' % self.by_id[s].get('text', '?'))

    # ---------- 象棋云库 (chessdb.cn) ----------
    def position_fen(self):
        """当前局面 → 云库 FEN；轮走方按上一着颜色推断。"""
        if self.d is None:
            return None
        st = self.board_at(self.cur)
        if self.cur < 0:
            roots = self.children.get(-1, [])
            first_red = self.by_id[roots[0]].get('red', True) if roots else True
            red_to_move = bool(first_red)
        else:
            m = self.by_id[self.cur]
            if m.get('red') is not None:
                red_to_move = not m['red']
            else:
                red_to_move = (m.get('depth', 1) % 2 == 0)
        return xqf.board_fen(st, red_to_move)

    def copy_fen(self):
        fen = self.position_fen()
        if not fen:
            self.set_status('未打开棋谱')
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(fen)
        self.set_status('FEN已复制并打开云库: ' + fen)
        # 查询页把整个查询串当作 FEN（空格需编码）
        webbrowser.open('https://www.chessdb.cn/query/?' + urllib.parse.quote(fen, safe='/'))

    def query_cloud(self):
        fen = self.position_fen()
        if not fen:
            self.set_status('未打开棋谱')
            return
        st = self.board_at(self.cur)
        self.set_status('正在查询云库分数…')
        self._cloud_result = None
        self._cloud_token = getattr(self, '_cloud_token', 0) + 1
        tok = self._cloud_token
        threading.Thread(target=self._cloud_worker, args=(fen, st, tok), daemon=True).start()
        self.root.after(100, lambda: self._cloud_poll(tok))

    def _cloud_worker(self, fen, st, tok):
        # 工作线程只做网络请求，不碰 tkinter；结果交主线程轮询取走
        url = ('https://www.chessdb.cn/chessdb.php?action=queryall&learn=1&showall=1&board='
               + urllib.parse.quote(fen))
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            txt = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'ignore')
            txt = txt.replace('\x00', '').strip()
            res = (fen, txt, st)
        except Exception as e:
            res = ('ERR', str(e))
        if tok == getattr(self, '_cloud_token', None):
            self._cloud_result = res

    def _cloud_poll(self, tok):
        if tok != getattr(self, '_cloud_token', None):
            return
        res = getattr(self, '_cloud_result', None)
        if res is None:
            self.root.after(100, lambda: self._cloud_poll(tok))
            return
        self._cloud_result = None
        if res[0] == 'ERR':
            self.set_status('云库查询失败: %s' % res[1])
        else:
            self._show_cloud(*res)

    def _show_cloud(self, fen, txt, st):
        if 'move:' not in txt:
            msg = {'unknown': '云库尚未收录此局面', 'checkmate': '此局面已是绝杀',
                   'invalid board': 'FEN 无效'}.get(txt, '云库返回: ' + txt[:80])
            self.set_status(msg)
            messagebox.showinfo('云库查询', msg, parent=self.root)
            return
        rows = []
        for ent in txt.split('|'):
            kv = {}
            for part in ent.split(','):
                if ':' in part:
                    k, v = part.split(':', 1)
                    kv[k.strip()] = v.strip()
            mv = kv.get('move', '')
            text = mv
            if len(mv) == 4:
                fx, fy = ord(mv[0]) - 97, int(mv[1])
                tx, ty = ord(mv[2]) - 97, int(mv[3])
                if (fx, fy) in st:
                    try:
                        text = xqf.move_text(st, fx, fy, tx, ty)
                    except Exception:
                        pass
            rows.append((text or mv, kv.get('score', ''), kv.get('rank', ''),
                         kv.get('winrate', ''), kv.get('drawrate', ''),
                         kv.get('lossrate', ''), kv.get('note', '')))
        if getattr(self, 'cloud_win', None) is not None:
            try:
                self.cloud_win.destroy()
            except Exception:
                pass
        w = tk.Toplevel(self.root)
        self.cloud_win = w
        side = '红方走棋' if fen.split(' ')[1] == 'w' else '黑方走棋'
        w.title('云库查询结果 · %s' % side)
        w.configure(bg=PANEL)
        tk.Label(w, text='%s（分数为正利于走子方；云库着法按分数排序）' % side,
                 bg=PANEL, fg='#4a4438', font=(UI_FONT, 10, 'bold')).pack(anchor='w', padx=8, pady=(8, 0))
        tk.Label(w, text=fen, bg=PANEL, fg='#9a8a6b', font=('Consolas', 9)).pack(anchor='w', padx=8)
        cols = ('move', 'score', 'rank', 'win', 'draw', 'loss', 'note')
        heads = ('着法', '分数', '等级', '胜率%', '和率%', '负率%', '备注')
        widths = (110, 60, 50, 65, 65, 65, 130)
        tv = ttk.Treeview(w, columns=cols, show='headings', height=14)
        for c, h, wd in zip(cols, heads, widths):
            tv.heading(c, text=h)
            tv.column(c, width=wd, anchor='center')
        tv.column('move', anchor='w')
        tv.tag_configure('best', background=CUR)
        for k, r in enumerate(rows[:30]):
            tv.insert('', 'end', values=r, tags=('best',) if k == 0 else ())
        sb = ttk.Scrollbar(w, orient='vertical', command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side='left', fill='both', expand=True, padx=(8, 0), pady=8)
        sb.pack(side='right', fill='y', pady=8, padx=(0, 8))
        b = rows[0]
        self.set_status('云库最佳: %s  分数%s  胜率%s%%（共%d个候选着法）' % (b[0], b[1], b[3], len(rows)))

    # ---------- 导航 ----------
    def go(self, i):
        self.commit_note(silent=True)
        self.cur = i
        self.selected = None
        self.draw_board()
        self.refresh_note()
        self.refresh_var_panel()
        if i >= 0 and self.tree.exists(str(i)):
            self.tree.selection_set(str(i))
            self.tree.see(str(i))
        m = self.by_id.get(i)
        if i < 0:
            self.cur_line.config(text='初始局面')
        else:
            n = len(path_to(self.by_id, i))
            self.cur_line.config(text='第 %d 着 · %s' % (n, m.get('text', '?')))
        self.update_dirty()

    def step(self, d):
        if self.d is None:
            return
        if d > 0:
            if self.cur in self.line:
                k = self.line.index(self.cur)
                if k + 1 < len(self.line):
                    self.go(self.line[k + 1])
            else:
                kids = self.children.get(self.cur, [])
                if kids:
                    self.go(kids[0])
        else:
            if self.cur >= 0:
                self.go(self.by_id[self.cur]['parent'])
            else:
                self.go(-1)

    def go_first(self):
        self.go(-1)

    def go_last(self):
        if self.line:
            self.go(self.line[-1])

    def toggle_play(self):
        if self.play_timer:
            self.root.after_cancel(self.play_timer); self.play_timer = None; return
        def tick():
            if self.cur in self.line:
                k = self.line.index(self.cur)
                if k + 1 < len(self.line):
                    self.go(self.line[k + 1])
                    self.play_timer = self.root.after(1200, tick)
                    return
            self.play_timer = None
        tick()

    def on_tree_select(self, e):
        sel = self.tree.selection()
        if sel and int(sel[0]) != self.cur:
            self.go(int(sel[0]))

    # ---------- 棋盘 ----------
    def draw_board(self):
        c = self.board_canvas
        c.delete('all')
        if self.d is None:
            return
        cell = self.cell.get()
        m = int(cell * 0.75)
        W = m * 2 + cell * 8
        H = m * 2 + cell * 9
        cw = c.winfo_width(); chh = c.winfo_height()
        scale = min((cw - 10) / W, (chh - 10) / H, 1.6)
        if scale <= 0:
            scale = 1
        cell *= scale; m = int(cell * 0.75)
        W = m * 2 + cell * 8; H = m * 2 + cell * 9
        ox = (cw - W) / 2; oy = (chh - H) / 2
        self._geom = (ox, oy, cell, m)
        X = lambda x: ox + m + x * cell
        Y = lambda y: oy + m + (9 - y) * cell
        c.create_rectangle(ox, oy, ox + W, oy + H, fill=BOARD_BG, outline='')
        for y in range(10):
            c.create_line(X(0), Y(y), X(8), Y(y), fill=BOARD_LINE, width=1.4)
        for x in range(9):
            if x in (0, 8):
                c.create_line(X(x), Y(0), X(x), Y(9), fill=BOARD_LINE, width=1.4)
            else:
                c.create_line(X(x), Y(0), X(x), Y(4), fill=BOARD_LINE, width=1.4)
                c.create_line(X(x), Y(5), X(x), Y(9), fill=BOARD_LINE, width=1.4)
        c.create_rectangle(X(0), Y(0), X(8), Y(9), outline=BOARD_LINE, width=2.5)
        for px, py in [(3, 0), (5, 0), (3, 9), (5, 9)]:
            c.create_line(X(px), Y(py), X(4), Y(py + (-1 if py else 1)), fill=BOARD_LINE, width=1.4)
        c.create_text(X(2), Y(4.5), text='楚 河', fill='#7a6a4f', font=(PIECE_FONT, int(cell * 0.5)))
        c.create_text(X(5.2), Y(4.5), text='汉 界', fill='#7a6a4f', font=(PIECE_FONT, int(cell * 0.5)))
        # 最后一步高亮
        if self.cur >= 0:
            lm = self.by_id[self.cur]
            for (px, py) in [lm['from'], lm['to']]:
                r = cell * 0.46
                c.create_rectangle(X(px) - r, Y(py) - r, X(px) + r, Y(py) + r,
                                   outline='#c8102e', width=2)
        # 选中格
        if self.selected:
            px, py = self.selected
            r = cell * 0.48
            c.create_rectangle(X(px) - r, Y(py) - r, X(px) + r, Y(py) + r, outline='#1a73e8', width=3)
        st = self.board_at(self.cur)
        r = cell * 0.44
        for (x, y), (kind, name) in st.items():
            red = kind.isupper()
            col = RED if red else BLK
            cx, cy = X(x), Y(y)
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill='#fdf6e3', outline=col, width=2)
            c.create_oval(cx - r * 0.78, cy - r * 0.78, cx + r * 0.78, cy + r * 0.78, outline=col, width=1)
            c.create_text(cx, cy, text=name, fill=col, font=(PIECE_FONT, int(r * 1.15), 'bold'))

    def _xy_from_event(self, e):
        if not hasattr(self, '_geom'):
            return None
        ox, oy, cell, m = self._geom
        x = round((e.x - ox - m) / cell)
        y = round(9 - (e.y - oy - m) / cell)
        if 0 <= x <= 8 and 0 <= y <= 9:
            return (x, y)
        return None

    def on_board_click(self, e):
        if self.d is None:
            return
        pos = self._xy_from_event(e)
        if pos is None:
            return
        st = self.board_at(self.cur)
        if self.selected is None:
            if pos in st:
                self.selected = pos
                self.draw_board()
            return
        if pos == self.selected:
            self.selected = None
            self.draw_board()
            return
        # 走子
        src = self.selected
        if src not in st:
            self.selected = None; self.draw_board(); return
        kind = st[src][0]
        played = len(path_to(self.by_id, self.cur))
        red_to_move = (played % 2 == 0)
        if kind.isupper() != red_to_move:
            self.set_status('现在轮到%s走子' % ('红方' if red_to_move else '黑方'))
            self.selected = None; self.draw_board(); return
        if pos in st and st[pos][0].isupper() == kind.isupper():
            # 点到己方另一子 -> 改选
            self.selected = pos; self.draw_board(); return
        self.add_move(src, pos)

    def add_move(self, src, dst):
        st = self.board_at(self.cur)
        text = xqf.move_text(st, src[0], src[1], dst[0], dst[1])
        new_i = (max(self.by_id) + 1) if self.by_id else 0
        m = {'i': new_i, 'parent': self.cur, 'from': list(src), 'to': list(dst),
             'piece': st[src], 'note': '', 'vars': [], 'text': text,
             'red': st[src][0].isupper(), 'depth': len(path_to(self.by_id, self.cur)) + 1}
        self.d['moves'].append(m)
        self.by_id[new_i] = m
        self.children.setdefault(self.cur, []).append(new_i)
        # 若当前着已有其它后续着法，新着作为变着：把显示线路切到新着
        if len(self.children[self.cur]) > 1:
            self.choice[self.cur] = new_i
        self.dirty = True
        self.rebuild_line()
        self.go(new_i)
        self.set_status('已输入：%s' % text)

    def delete_move(self):
        if self.d is None or self.cur < 0:
            return
        # 删除当前着及其子树
        to_del = set()
        stack = [self.cur]
        while stack:
            x = stack.pop(); to_del.add(x)
            stack += self.children.get(x, [])
        par = self.by_id[self.cur]['parent']
        self.d['moves'] = [m for m in self.d['moves'] if m['i'] not in to_del]
        self.by_id = {m['i']: m for m in self.d['moves']}
        self.children = {}
        for m in self.d['moves']:
            self.children.setdefault(m['parent'], []).append(m['i'])
        self.choice = {k: v for k, v in self.choice.items() if v in self.by_id}
        self.dirty = True
        self.rebuild_line()
        self.go(par)
        self.set_status('已删除本步及其分支')

    # ---------- 注解 ----------
    def refresh_note(self):
        self.note_txt.delete('1.0', 'end')
        if self.d is None:
            return
        if self.cur < 0:
            self.note_head.config(text='整局注解')
            self.note_txt.insert('1.0', self.d.get('game_note', ''))
        else:
            m = self.by_id[self.cur]
            self.note_head.config(text='第 %d 着 %s 的注解' % (len(path_to(self.by_id, self.cur)), m.get('text', '')))
            self.note_txt.insert('1.0', m.get('note', ''))

    def commit_note(self, silent=False):
        if self.d is None:
            return
        txt = self.note_txt.get('1.0', 'end-1c')
        if self.cur < 0:
            if txt != self.d.get('game_note', ''):
                self.d['game_note'] = txt; self.dirty = True
        else:
            m = self.by_id.get(self.cur)
            if m and txt != m.get('note', ''):
                m['note'] = txt; self.dirty = True
                self.rebuild_line()   # 刷新 * 标记
        self.update_dirty()

    # ---------- 文件 ----------
    def open_file(self):
        p = filedialog.askopenfilename(filetypes=[('XQF 棋谱', '*.xqf *.XQF'), ('所有文件', '*.*')])
        if p:
            self.load(p)

    def save_file(self):
        if self.d is None:
            return
        self.commit_note(silent=True)
        if not self.path:
            return self.save_as()
        try:
            orig = self.path if (self.path and os.path.exists(self.path)) else None
            xqf_write.write(self.d, self.path, original_path=orig)
            self.dirty = False; self.update_dirty()
            self.set_status('已保存 %s' % self.path)
        except Exception as e:
            messagebox.showerror('保存失败', str(e))

    def save_as(self):
        if self.d is None:
            return
        self.commit_note(silent=True)
        p = filedialog.asksaveasfilename(defaultextension='.xqf',
                                         filetypes=[('XQF 棋谱', '*.xqf')])
        if p:
            try:
                orig = self.path if (self.path and os.path.exists(self.path)) else None
                xqf_write.write(self.d, p, original_path=orig)
                self.path = p; self.dirty = False; self.update_dirty()
                self.set_status('已另存为 %s' % p)
            except Exception as e:
                messagebox.showerror('保存失败', str(e))

    def new_file(self):
        self.commit_note(silent=True)
        if self.dirty and not messagebox.askyesno('新建', '当前棋谱未保存，确定新建？'):
            return
        d = {'info': {'title': '新棋谱', 'version': 18, 'result': 0, 'type': 1},
             'init_board': {}, 'moves': [], 'game_note': ''}
        # 标准开局
        arr = xqf_write.standard_board32()
        for side in range(2):
            nm = xqf.RED_NAME if side == 0 else xqf.BLK_NAME
            for mi in range(16):
                p = arr[side * 16 + mi]
                if p == 0xFF:
                    continue
                kind = xqf.PIECE_KIND[mi]
                d['init_board'][(p // 10, p % 10)] = (kind if side == 0 else kind.lower(), nm[kind])
        self.d = d; self.path = None
        self.by_id = {}; self.children = {-1: []}
        self.cur = -1; self.selected = None; self.dirty = True
        self.choice = {}
        self.title_lbl.config(text='新棋谱（0 着）')
        self.rebuild_line(); self.go(-1)
        self.set_status('新建棋谱：点击棋盘走子输入')

    # ---------- 导入 / 合并 / 批量转换 ----------
    def _refresh_from_dict(self, title, dirty):
        self.by_id = {m['i']: m for m in self.d['moves']}
        self.children = {}
        for m in self.d['moves']:
            self.children.setdefault(m['parent'], []).append(m['i'])
        self.dirty = dirty
        self.choice = {}
        self.title_lbl.config(text='%s（%d 着）' % (title, len(self.d['moves'])))
        self.rebuild_line(); self.update_dirty()

    def import_text(self):
        p = filedialog.askopenfilename(
            title='导入文本棋谱',
            filetypes=[('文本棋谱', '*.txt *.chn *.ccm *.mxq *.che'), ('所有文件', '*.*')])
        if not p:
            return
        try:
            d = xqf_import.parse_text(xqf_import._read_text(p))
            d['info']['title'] = os.path.splitext(os.path.basename(p))[0]
            d = xqf.annotate(d)
        except Exception as e:
            messagebox.showerror('导入失败', str(e)); return
        self.commit_note(silent=True)
        self.d = d; self.path = None; self.cur = -1; self.selected = None
        self._refresh_from_dict(d['info']['title'], True)
        self.go(-1)
        self.set_status('已导入 %s（%d 着），可「另存为」XQF' % (p, len(d['moves'])))

    def merge_xqf(self):
        if not getattr(self, 'd', None):
            self.new_file()
        paths = filedialog.askopenfilenames(title='选择要合并的棋谱文件（可多选）',
                                            filetypes=[('XQF 棋谱', '*.xqf *.XQF'), ('所有文件', '*.*')])
        if not paths:
            return
        self.commit_note(silent=True)
        total = 0
        ok, fail = [], []
        for p in paths:
            try:
                total += xqf_write.merge(self.d, p)
                ok.append(os.path.basename(p))
            except Exception as e:
                fail.append('%s: %s' % (os.path.basename(p), e))
        if not ok:
            messagebox.showerror('合并失败', '\n'.join(fail) or '没有可合并的文件'); return
        self.d = xqf.annotate(self.d)
        self._refresh_from_dict(self.d['info'].get('title') or '棋谱', True)
        self.go(self.cur)
        msg = '已合并 %d 个文件（新增 %d 着，相同着法共用、不同处分支）：%s' % (len(ok), total, '、'.join(ok))
        if fail:
            msg += '\n失败：' + '；'.join(fail)
            messagebox.showwarning('部分合并失败', msg)
        self.set_status('已合并 %d 个文件，新增 %d 着' % (len(ok), total))

    def batch_convert_gui(self):
        folder = filedialog.askdirectory(title='选择含非 XQF 棋谱的文件夹')
        if not folder:
            return
        out = filedialog.askdirectory(title='选择输出文件夹（取消则存回原文件夹）') or folder
        try:
            ok, fail = xqf_import.batch_convert(folder, out)
        except Exception as e:
            messagebox.showerror('批量转换失败', str(e)); return
        msg = '成功转换 %d 个：\n' % len(ok)
        msg += '\n'.join('%s（%d 着）' % (f, n) for f, n in ok[:30])
        if fail:
            msg += '\n\n失败 %d 个：\n' % len(fail)
            msg += '\n'.join('%s: %s' % (f, e) for f, e in fail[:20])
        messagebox.showinfo('批量转换结果', msg)
        self.set_status('批量转换：成功 %d，失败 %d' % (len(ok), len(fail)))

    # ---------- 查找浏览 XQF / 导出棋子位置 ----------
    def find_games(self, target=None):
        dlg = getattr(self, '_find_dlg', None)
        if dlg is not None and dlg.winfo_exists():
            if target is not None:
                dlg.set_target(target)
            dlg.lift()
            return dlg
        self._find_dlg = xqfind.FindDialog(self, target=target)
        return self._find_dlg

    def export_position(self):
        """右键菜单：导出棋子位置到文件查找窗口（XQStudio 同名功能）。"""
        if self.d is None:
            self.set_status('未打开棋谱')
            return None
        return self.find_games(target=self.board_at(self.cur))

    def on_board_right_click(self, e):
        menu = tk.Menu(self.root, tearoff=0, font=(UI_FONT, 10))
        menu.add_command(label='导出棋子位置到查找窗口', command=self.export_position)
        menu.add_command(label='查找浏览 XQF 文件…', command=self.find_games)
        menu.add_separator()
        menu.add_command(label='云库分数（当前局面）', command=self.query_cloud)
        menu.add_command(label='复制云库 FEN', command=self.copy_fen)
        menu.tk_popup(e.x_root, e.y_root)

    # ---------- 杂项 ----------
    def on_fs_move(self, e):
        fs = self.fs_move.get()
        self.style.configure('Treeview', font=(UI_FONT, fs), rowheight=int(fs * 1.7))

    def on_fs_note(self, e):
        self.note_txt.configure(font=(UI_FONT, self.fs_note.get()))

    def update_dirty(self):
        self.dirty_lbl.config(text='● 未保存' if self.dirty else '')

    def set_status(self, s):
        self.status.config(text=s)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()