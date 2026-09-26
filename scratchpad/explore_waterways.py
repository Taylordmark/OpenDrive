import json
d = json.load(open('data/raw/summit_county_test/osm.json'))
els = d['elements']
ways = [e for e in els if e['type']=='way']
wtags = {}
for w in ways:
    t = w.get('tags',{})
    if 'waterway' in t:
        wtags.setdefault(t['waterway'], []).append((t.get('name','(unnamed)'), t.get('width'), w['id'], len(w.get('nodes',[]))))
for k,v in wtags.items():
    print(k, len(v))
    for name,width,wid,n in v[:60]:
        print('   ', wid, name, 'width=',width,'nodes=',n)
