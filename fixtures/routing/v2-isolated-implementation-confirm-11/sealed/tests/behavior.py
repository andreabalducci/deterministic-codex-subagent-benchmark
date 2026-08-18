import importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); s=importlib.util.spec_from_file_location('candidate',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
assert str(m.safe_member('images/a.png'))=='images/a.png'
for value in ('../secret','..\\secret','C:\\secret'):
 try: m.safe_member(value); raise AssertionError(value+' accepted')
 except ValueError: pass
