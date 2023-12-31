import numpy as np
import networkx as nx
import scipy.sparse as sp
import itertools
from joblib import Parallel, delayed
from utils.constants import BIG_NUMBER


def add_features(graph, node_features, edge_features):
    """
    Adds given features to the provided base graph. This function creates a copy of the graph.
    Each edge 'feature' input is a dictionary mapping feature names to a V x D matrix. This matrix
    holds feature values in a padded-adjacency-list format. The pad value is equal to
    to the number of nodes in the graph.
    """
    graph = graph.copy()

    # Simple error handling
    if node_features is None:
        node_features = {}

    if edge_features is None:
        edge_features = {}

    # Add node features
    for name, values in node_features.items():
        values = values.flatten()
        for node in graph.nodes():
            v = {name: float(values[node])}
            graph.add_node(node, **v)

    # Add edge features
    adj_lst, _ = adjacency_list(graph)
    edge_attr = {(src, dst, 0): {} for src, dst in graph.edges()}
    for name, values in edge_features.items():
        for node in graph.nodes():
            lst = adj_lst[node]
            for i, neighbor in enumerate(lst):
                v = {name: float(values[node, i])}
                graph.add_edge(node, neighbor, key=0, **v)

    return graph


def max_degrees(graphs, k, unique_neighborhoods=True):
    adj_matrices = [nx.adjacency_matrix(graph) for graph in graphs]

    max_degrees = np.zeros(shape=(k+1,))
    for adj in adj_matrices:
        neighborhoods = random_walk_neighborhoods(adj, k=k, unique_neighborhoods=unique_neighborhoods)
        degrees = [np.max(mat.sum(axis=-1)) for mat in neighborhoods]
        max_degrees = np.maximum(max_degrees, degrees)

    return max_degrees


def pad_adj_list(adj_lst, max_degree, max_num_nodes,  mask_number):
    padded = []
    for lst in adj_lst:
        pd = np.pad(lst, pad_width=(0, max_degree-len(lst)),
                    mode='constant', constant_values=mask_number)
        padded.append(pd)

    while len(padded) <= max_num_nodes:
        padded.append(np.full(shape=(max_degree, ), fill_value=mask_number))

    # Returns a max_num_nodes x max_degree numpy array
    return np.array(padded)


def neighborhood_adj_lists(neighborhoods, max_degrees, max_num_nodes, mask_number):
    neighborhood_lists = []
    for neighborhood, degree in zip(neighborhoods, max_degrees):
        adj_lst = adj_matrix_to_list(neighborhood)
        adj_lst = pad_adj_list(adj_lst=adj_lst,
                               max_degree=degree,
                               mask_number=mask_number,
                               max_num_nodes=max_num_nodes)
        neighborhood_lists.append(adj_lst)

    return neighborhood_lists


def adj_matrix_to_list(adj_matrix, inverted=False):
    if inverted:
        adj_matrix = adj_matrix.transpose(copy=True)

    rows, cols = adj_matrix.nonzero()

    adj_dict = {}
    for r, c in zip(rows, cols):
        if r not in adj_dict:
            adj_dict[r] = []
        adj_dict[r].append(c)

    # Create adjacency list
    adj_lst = []
    for node in sorted(adj_dict.keys()):
        adj_lst.append(list(sorted(adj_dict[node])))

    return adj_lst


def random_walk_neighborhoods(adj_matrix, k, unique_neighborhoods=True):
    mat = sp.eye(adj_matrix.shape[0], format='csr')
    neighborhoods = [mat]
    agg_mat = mat

    for _ in range(k):
        mat = mat.dot(adj_matrix)
        mat.data[:] = 1

        if unique_neighborhoods:
            # Remove already reached nodes
            mat = mat - agg_mat
            mat.data = np.maximum(mat.data, 0)
            mat.eliminate_zeros()
            mat.data[:] = 1

            agg_mat += mat
            agg_mat.data[:] = 1

        neighborhoods.append(mat)

    return neighborhoods


def adjacency_list(graph):
    adj_lst = list(map(lambda x: list(sorted(x)), iter(graph.adj.values())))
    max_degree = max(map(lambda x: len(x), adj_lst))
    return adj_lst, max_degree


def simple_paths(graph, sources, sinks, max_num_paths):
    cutoff = nx.diameter(graph)

    # Dictionary from (source, sink) to list of paths
    all_paths = {}

    def compute_paths(source, sink, cutoff, max_num_paths):
        paths = list(nx.all_simple_paths(graph, source, sink, cutoff=cutoff))
        paths = sorted(paths, key=len)[:max_num_paths]
        return {(source, sink): paths}

    result = Parallel(n_jobs=-1)(delayed(compute_paths)(source, sink, cutoff, max_num_paths) for source, sink in itertools.product(sources, sinks))

    for paths in result:
        all_paths.update(paths)

    return all_paths


def random_sources_sinks(graph, num_sources, num_sinks):
    nodes = np.random.choice(a=list(graph.nodes()), size=num_sources+num_sinks, replace=False)
    return nodes[:num_sources], nodes[num_sources:]


def farthest_nodes(graph, num_sources, num_sinks):
    """
    Returns a list of sources and sinks in which the total distance between all nodes
    is maximizes. Distance refers to unweighted shortest path length.
    """
    n_nodes = graph.number_of_nodes()
    start = np.random.randint(low=0, high=n_nodes)
    nodes = [start]

    lengths = {}
    threshold = min(n_nodes, num_sources + num_sinks)
    while len(nodes) < threshold:

        max_node = None
        max_len = -BIG_NUMBER
        for u in graph.nodes():

            # Minimum distance to any of the already-selected nodes
            min_len = BIG_NUMBER
            for v in nodes:
                if (u, v) in lengths:
                    length = lengths[(u, v)]
                elif (v, u) in lengths:
                    length = lengths[(v, u)]
                else:
                    forward_path_length = nx.shortest_path_length(graph, source=u, target=v)
                    backward_path_length = nx.shortest_path_length(graph, source=v, target=u)
                    path_length = min(forward_path_length, backward_path_length)

                    lengths[(u, v)] = path_length
                    length = path_length
                min_len = min(min_len, length)

            # Select node whose closest distance to any selected vertex
            # is maximized
            if min_len > max_len:
                max_len = min_len
                max_node = u

        print('{0}, Len: {1}'.format(max_node, max_len))

        assert max_node is not None, 'No node found.'
        nodes.append(max_node)

    return nodes[:num_sources], nodes[num_sources:]


def farthest_sink_nodes(graph, num_sources, num_sinks):
    """
    Generates sources and sinks such that the sinks are as far as possible from the randomly
    generated source nodes. Differs from the function 'farthest nodes' by not enforcing
    a large pairwise distance between sources (or sinks) themselves.
    """

    sources = np.random.choice(a=list(graph.nodes()), replace=False, size=num_sources)

    node_distances = []
    for u in sorted(graph.nodes()):
        distances = [nx.shortest_path_length(graph, source=u, target=v) for v in sources]
        node_distances.append(distances)

    min_distances = np.amin(a=node_distances, axis=-1)
    sinks = np.argsort(-min_distances)[:num_sinks]

    return sources, sinks
