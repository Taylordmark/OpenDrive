import json, numpy as np, sys
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from verify_route_graph import load_graph, MASKS, AREA
nodes, edges = load_graph(MASKS/f"routegraph_{AREA}_test.bin"); n=len(nodes)
nc,lab=connected_components(coo_matrix((edges['l'],(edges['a'],edges['b'])),shape=(n,n)),directed=False)
nt=cKDTree(nodes)
d=json.load(open('../../data/road_tiles/summit_county_test/road_stations.json')); S=d['segments']
xy=[];seg=[];z=[];off=0;ends=[]
for k,s in enumerate(S):
    m=len(s['east_m']); xy.append(np.stack([s['east_m'],s['north_m']],1)); seg+=[k]*m; z+=s['elev_m']; ends+=[off,off+m-1]; off+=m
xy=np.concatenate(xy); seg=np.array(seg); z=np.array(z); t=cKDTree(xy)
k0=int(sys.argv[1]); i0=int(sys.argv[2]); R=float(sys.argv[3])
starts=np.concatenate([[0],np.flatnonzero(seg[1:]!=seg[:-1])+1])
c=xy[starts[k0]+i0]
for e in ends:
    if np.hypot(*(xy[e]-c))>R: continue
    ii=[j for j in t.query_ball_point(xy[e],60) if seg[j]!=seg[e]]
    near=sorted(((np.hypot(*(xy[j]-xy[e])),seg[j],z[j]-z[e]) for j in ii))[:1]
    s=S[seg[e]]; _,ni=nt.query((xy[e][0]*100,-xy[e][1]*100))
    print(f"seg{seg[e]} {'start' if e==starts[seg[e]] else 'end'} n={len(s['east_m'])} {s['highway_class'][0]} '{s['road_name'][0]}' comp={lab[ni]} z={z[e]:.1f} nearest-other={[(round(a,1),b,round(c2,1)) for a,b,c2 in near]}")
