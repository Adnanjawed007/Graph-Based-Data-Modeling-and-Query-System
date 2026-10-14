"""Persist the NetworkX projection: python -m backend.build_graph"""
from .app import graph
from .storage import persist_graph

if __name__=='__main__':
    nodes,edges=persist_graph(graph())
    print(f'Persisted {nodes} canonical entities and {edges} validated relationships.')
