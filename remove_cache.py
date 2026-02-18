import pathlib, shutil, sys
for d in pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").rglob("__pycache__"):
    shutil.rmtree(d)
    print(f"Removed {d}")
