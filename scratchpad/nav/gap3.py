import json, numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from verify_route_graph import load_graph, MASKS, AREA, load_towns, nearest
nodes, edges = load_graph(MASKS/f"routegraph_{AREA}_test.bin"); n=len(nodes)
nc,lab=connected_components(coo_matrix((edges['l'],(edges['a'],edges['b'])),shape=(n,n)),directed=False)
t=load_towns(); c=lab[nearest(nodes,t['Silverthorne'])]
d=json.load(open('../../data/road_tiles/summit_county_test/road_stations.json')); S=d['segments']
xy=[];seg=[];z=[];st=[]
for k,s in enumerate(S):
    xy.append(np.stack([s['east_m'],s['north_m']],1)); seg+=[k]*len(s['east_m']); z+=s['elev_m']; st+=[bool(a or b) for a,b in zip(s['is_struct'],s['is_tunnel'])]
xy=np.concatenate(xy); seg=np.array(seg); z=np.array(z); st=np.array(st)
nt=cKDTree(nodes); _,ni=nt.query(np.stack([xy[:,0]*100,-xy[:,1]*100],1)); L=lab[ni]
A=np.flatnonzero(L==c); B=np.flatnonzero(L==0)
tb=cKDTree(xy[B]); dd,jj=tb.query(xy[A])
o=np.argsort(dd); seen=set()
for i in o:
    a=A[i]; b=B[jj[i]]; key=(seg[a],seg[b])
    if key in seen: continue
    seen.add(key)
    sa,sb=S[seg[a]],S[seg[b]]
    print(f"{dd[i]:.1f}m dz={z[b]-z[a]:.1f} '{sa['road_name'][0]}'({sa['highway_class'][0]},st={st[a]}) vs '{sb['road_name'][0]}'({sb['highway_class'][0]},st={st[b]})")
    if len(seen)>15: break
