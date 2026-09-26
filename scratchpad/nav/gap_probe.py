import numpy as np, json
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from verify_route_graph import load_graph, MASKS, AREA
nodes, edges = load_graph(MASKS/f"routegraph_{AREA}_test.bin")
n=len(nodes)
m=coo_matrix((edges['l'],(edges['a'],edges['b'])),shape=(n,n))
nc,lab=connected_components(m,directed=False)
sizes=np.bincount(lab); order=np.argsort(-sizes)
print('top comps', [(int(c),int(sizes[c])) for c in order[:10]])
d=json.load(open(str(MASKS.parents[2].parent/'data/road_tiles/summit_county_test/road_stations.json')))
S=d['segments']
xy=[];z=[];info=[]
for k,s in enumerate(S):
    for i in range(len(s['east_m'])):
        xy.append((s['east_m'][i]*100,-s['north_m'][i]*100)); z.append(s['elev_m'][i]); info.append((k,i,len(s['east_m']),s['is_bridge'][i],s['is_struct'][i],s['road_name'][i],s['highway_class'][i]))
xy=np.array(xy); z=np.array(z)
st=cKDTree(xy)
big=order[0]
tb=cKDTree(nodes[lab==big])
for c in order[1:8]:
    pts=nodes[lab==c]
    dd,ii=tb.query(pts)
    j=np.argmin(dd); p=pts[j]; q=nodes[lab==big][ii[j]]
    _,sp=st.query(p); _,sq=st.query(q)
    print(f'comp {c} size {sizes[c]}: closest gap {dd[j]/100:.1f} m; A={info[sp]} z={z[sp]:.1f}; B={info[sq]} z={z[sq]:.1f}')
