from pathlib import Path
def safe_member(name):
    path = Path(name)
    if '..' in path.parts: raise ValueError('traversal')
    return path
