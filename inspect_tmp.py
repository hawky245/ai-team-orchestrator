import json
from pathlib import Path
from collections import defaultdict
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions

extraction = json.loads(Path('graphify-out/.graphify_extract.json').read_text(encoding="utf-8"))
G = build_from_json(extraction, root='.', directed=False)
communities = cluster(G)
cohesion = score_all(G, communities)
gods = god_nodes(G)
surprises = surprising_connections(G, communities)
questions = suggest_questions(G, communities, {cid: 'Community '+str(cid) for cid in communities})

comm_nodes = defaultdict(list)
for cid, nodes in communities.items():
    comm_nodes[cid].extend(nodes)
print('Number of communities:', len(comm_nodes))
for cid, nodes in sorted(comm_nodes.items(), key=lambda x: -len(x[1])):
    print('Community', cid, 'size', len(nodes), 'cohesion', round(cohesion.get(cid,0),3))
    for n in nodes[:4]:
        nd = G.nodes[n]
        print('   ', n, '|', nd.get('label',''), '|', nd.get('file_type',''))

print('\n--- GOD NODES ---')
for g in gods[:5]:
    print(' ', g)
print('\n--- SURPRISES ---')
for s in surprises[:5]:
    print(' ', s)
print('\n--- QUESTIONS ---')
for q in questions[:5]:
    print(' ', q)