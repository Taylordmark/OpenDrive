import json, numpy as np
from scipy.spatial import cKDTree
d=json.load(open('../../data/road_tiles/summit_county_test/road_stations.json'))
S=d['segments']
xy=[];seg=[];z=[];br=[];ends=[];off=0;hc=[]
for k,s in enumerate(S):
    n=len(s['east_m']); xy.append(np.stack([s['east_m'],s['north_m']],1)); seg+=[k]*n; z+=s['elev_m']; br+=s['is_bridge']; hc+=s['highway_class']
    ends+=[off,off+n-1]; off+=n
xy=np.concatenate(xy); seg=np.array(seg); z=np.array(z); br=np.array(br)
print('is_bridge fraction', br.mean())
t=cKDTree(xy); dzs=[]
for e in ends:
    ii=[j for j in t.query_ball_point(xy[e],12) if seg[j]!=seg[e]]
    if ii:
        j=min(ii,key=lambda j: np.hypot(*(xy[j]-xy[e]))); dzs.append((abs(z[j]-z[e]), hc[e], hc[j]))
dz=np.array([a for a,_,_ in dzs])
print(len(dz), np.percentile(dz,[50,75,90,95,99]))
for thr in (5,8,12,20): print(thr,(dz>thr).sum())
big=[x for x in dzs if x[0]>5]
from collections import Counter
print(Counter((a,b) for _,a,b in big).most_common(12))
