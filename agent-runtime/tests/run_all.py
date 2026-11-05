"""一次跑完所有测试：python tests/run_all.py"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

files = sorted(name for name in os.listdir(HERE) if name.startswith('test_') and name.endswith('.py'))
failed = 0
# Windows 控制台默认 GBK，子进程里强制 UTF-8，否则中文输出会乱码
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
for name in files:
    result = subprocess.run([sys.executable, os.path.join(HERE, name)], env=env)
    if result.returncode != 0:
        failed += 1
        print("FAIL %s" % name)

print("-" * 40)
print("全部通过" if failed == 0 else "失败 %d 个" % failed)
sys.exit(1 if failed else 0)
