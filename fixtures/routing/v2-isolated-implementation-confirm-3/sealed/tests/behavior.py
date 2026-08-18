import copy, importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); s=importlib.util.spec_from_file_location('candidate',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
class Clock:
 def __init__(self): self.now=0
 def __call__(self): return self.now
c=Clock(); cache=m.TtlCache(5,c); original={'items':[1]}; cache.put('k',original); original['items'].append(2); assert cache.get('k')=={'items':[1]}; got=cache.get('k'); got['items'].append(3); assert cache.get('k')=={'items':[1]}; c.now=5
try: cache.get('k'); raise AssertionError('deadline accepted')
except KeyError: pass
try: m.TtlCache(0,c); raise AssertionError('zero ttl accepted')
except ValueError: pass
