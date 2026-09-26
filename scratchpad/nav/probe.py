import json, numpy as np
from scipy.spatial import cKDTree
d=json.load(open('../data/road_tiles/summit_county_test/road_stations.json'))
S=d['segments']
xy=[];seg=[];z=[];st=[];ends=[]
off=0
for k,s in enumerate(S):
    n=len(s['east_m']); xy.append(np.stack([s['east_m'],s['north_m']],1)); seg+= [k]*n; z+=s['elev_m']
    st += [bool(a or b or c) for a,b,c in zip(s['is_bridge'],s['is_tunnel'],s['is_struct'])]
    ends += [off, off+n-1]; off+=n
xy=np.concatenate(xy); seg=np.array(seg); z=np.array(z); st=np.array(st)
sp=np.hypot(*np.diff(xy,axis=0).T); same=seg[1:]==seg[:-1]
print('spacing', np.percentile(sp[same],[1,50,99]))
t=cKDTree(xy)
for R in (3,6,10,15):
    c=0
    for e in ends:
        ii=t.query_ball_point(xy[e],R); c+= any(seg[j]!=seg[e] for j in ii)
    print('endpoint join R',R, c,'/',len(ends))
pairs=t.query_pairs(4.0,output_type='ndarray')
m=(seg[pairs[:,0]]!=seg[pairs[:,1]])
print('cross pairs diffseg',m.sum())
p=pairs[m]; dz=np.abs(z[p[:,0]]-z[p[:,1]])
print('dz pct',np.percentile(dz,[50,90,99]), (dz>3).sum(), 'struct', (st[p[:,0]]|st[p[:,1]]).sum())
