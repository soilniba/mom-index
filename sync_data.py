"""数据同步脚本 — 从 data/ 同步到 frontend/data/
运行: python sync_data.py
pipeline.py 已内置自动同步，此脚本用于手动修复不一致。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
FRONTEND_DATA_DIR = os.path.join(HERE, "frontend", "data")
FILES = ["dashboard_data.json", "history.json", "xhs_posts.json", "etf_oi.json"]


def publish_etf_oi(data_dir, frontend_data_dir=None):
    """原子发布ETF历史；读取或替换失败时保留网页旧文件。"""
    source = Path(data_dir) / 'etf_oi.json'
    if not source.is_file():
        return False
    target = Path(frontend_data_dir or FRONTEND_DATA_DIR) / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False) as output:
            temporary = output.name
            with source.open('rb') as original:
                shutil.copyfileobj(original, output)
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return True

def sync():
    if not os.path.isdir(DATA_DIR):
        print(f"错误: 数据目录不存在 {DATA_DIR}", file=sys.stderr)
        sys.exit(1)
    os.makedirs(FRONTEND_DATA_DIR, exist_ok=True)
    for fname in FILES:
        src = os.path.join(DATA_DIR, fname)
        dst = os.path.join(FRONTEND_DATA_DIR, fname)
        if os.path.exists(src):
            if fname == 'etf_oi.json':
                publish_etf_oi(DATA_DIR)
            else:
                shutil.copy2(src, dst)
            size = os.path.getsize(dst)
            print(f"  ✓ {fname} ({size} bytes)")
        else:
            print(f"  ○ {fname} (源文件不存在，跳过)")
    print(f"\n同步完成: {DATA_DIR} → {FRONTEND_DATA_DIR}")

if __name__ == "__main__":
    sync()
