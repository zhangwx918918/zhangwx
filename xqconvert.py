# -*- coding: utf-8 -*-
"""命令行棋谱工具（无需打开界面）。
用法：
  python xqconvert.py convert  输入.txt  输出.xqf      # 单个文本棋谱转 XQF
  python xqconvert.py batch    文件夹  [输出文件夹]     # 批量转换 txt/chn/ccm/mxq/che
  python xqconvert.py merge    主谱.xqf  其它.xqf  输出.xqf   # 合并两份 XQF（多局共存）
"""
import sys
import xqf
import xqf_write
import xqf_import


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    cmd = sys.argv[1]
    if cmd == 'convert' and len(sys.argv) >= 4:
        n = xqf_import.convert_file(sys.argv[2], sys.argv[3])
        print('已转换 %s -> %s（%d 着）' % (sys.argv[2], sys.argv[3], n))
    elif cmd == 'batch' and len(sys.argv) >= 3:
        out = sys.argv[3] if len(sys.argv) > 3 else None
        ok, fail = xqf_import.batch_convert(sys.argv[2], out)
        print('成功 %d 个：' % len(ok))
        for f, n in ok:
            print('  %s（%d 着）' % (f, n))
        if fail:
            print('失败 %d 个：' % len(fail))
            for f, e in fail:
                print('  %s: %s' % (f, e))
    elif cmd == 'merge' and len(sys.argv) >= 5:
        d = xqf.parse(sys.argv[2])
        added = xqf_write.merge(d, sys.argv[3])
        xqf_write.write(d, sys.argv[4], original_path=sys.argv[2])
        print('已合并 %s + %s -> %s（新增 %d 着，共 %d 着）'
              % (sys.argv[2], sys.argv[3], sys.argv[4], added, len(d['moves'])))
    else:
        print(__doc__); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())