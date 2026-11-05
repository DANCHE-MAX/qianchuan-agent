"""临时工具：看一眼后台导出的 xlsx 长什么样（表头、单位、行数）。

用法：python tools/inspect_xlsx.py example
"""
import glob
import os
import sys

import pandas as pd


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "example"
    paths = sorted(glob.glob(os.path.join(folder, "*.xlsx")))
    if not paths:
        print("没找到 xlsx")
        return 1

    for path in paths:
        print("=" * 78)
        print("FILE:", os.path.basename(path))
        try:
            df = pd.read_excel(path, sheet_name=0, dtype=str)
        except Exception as exc:
            print("  读取失败：%s" % exc)
            continue

        print("SHAPE:", df.shape)
        print("COLUMNS (%d):" % len(df.columns))
        for i, col in enumerate(df.columns):
            print("  %2d  %s" % (i, col))

        print("SAMPLE (前 3 行，前 12 列):")
        for idx, row in df.head(3).iterrows():
            vals = []
            for col in list(df.columns)[:12]:
                v = row[col]
                vals.append("%s=%s" % (col, ("NULL" if pd.isna(v) else str(v))[:18]))
            print("  ", " | ".join(vals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
