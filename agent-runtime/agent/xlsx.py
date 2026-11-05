"""零依赖读取 xlsx。

为什么不用 pandas / openpyxl：
  用户的 Python 是刚装的 3.12.2，没装任何第三方库，pip 也可能不通。
  xlsx 本质是一个 zip 包里的 XML，用标准库就能读，别为了读个表格去装一堆依赖。

支持：共享字符串、inline 字符串、稀疏单元格（缺失的格子补空）。
不支持：公式求值（读的是缓存值）、合并单元格、多工作表（默认第一个）。
"""
import re
import xml.etree.ElementTree as ET
import zipfile

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def col_index(ref):
    """把 "AB12" 转成 27（0 基）。"""
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_rows(path, sheet_index=0):
    """返回 list[list[str]]，第一行是表头。"""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()

        shared = []
        if "xl/sharedStrings.xml" in names:
            with z.open("xl/sharedStrings.xml") as f:
                for _, el in ET.iterparse(f):
                    if el.tag == NS + "si":
                        shared.append("".join(t.text or "" for t in el.iter(NS + "t")))
                        el.clear()

        sheets = sorted(n for n in names
                        if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        if not sheets:
            raise RuntimeError("这个文件里找不到工作表，可能不是 xlsx")
        target = sheets[min(sheet_index, len(sheets) - 1)]

        rows = []
        with z.open(target) as f:
            for _, el in ET.iterparse(f):
                if el.tag != NS + "row":
                    continue
                cells = {}
                for c in el.iter(NS + "c"):
                    idx = col_index(c.get("r"))
                    kind = c.get("t")
                    v = c.find(NS + "v")
                    if kind == "s" and v is not None and v.text is not None:
                        val = shared[int(v.text)]
                    elif kind == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter(NS + "t"))
                    else:
                        val = v.text if v is not None else ""
                    cells[idx] = val
                if cells:
                    width = max(cells) + 1
                    rows.append([cells.get(i, "") for i in range(width)])
                el.clear()
        return rows
