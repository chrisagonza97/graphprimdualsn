import networkx as nx
import numpy as np
import scipy.sparse as sp
from os import path
from utils.constants import SMALL_NUMBER
from utils.utils import serialize_dict, deserialize_dict
from annoy import AnnoyIndex


def load_to_networkx(path):
    graph = nx.DiGraph()

    edge_features = {}

    with open(path, 'r') as net_file:
        metadata = True

        nodes = set()

        for line in net_file:
            if len(line) == 0:
                continue
            if line.startswith('~'):
                metadata = False
                continue
            if metadata:
                continue

            edge_elems = line.split('\t')
            init, term, features = _parse_edge_features(edge_elems)

            if init == -1 or term == -1:
                continue

            nodes.add(init)
            nodes.add(term)

            edge = (init, term)
            edge_features[edge] = features

    # Rename nodes 0,...,n-1. Sort node set to ensure a deterministic naming.
    node_map = {}
    for i, node in enumerate(sorted(nodes)):
        node_map[node] = i
        graph.add_node(i)

    # Add edges features
    for (src, dst), features in edge_features.items():
        graph.add_edge(node_map[src], node_map[dst], **features)

    # We make the graph strongly connected to ensure that any combination of source / sink
    # constitutes a valid problem
    comp = nx.strongly_connected_components(graph)
    try:
        c1 = list(comp.__next__())
        while True:
            c2 = list(comp.__next__())

            # For now, we don't import any edge features
            graph.add_edge(c1[0], c2[0])
            graph.add_edge(c2[0], c1[0])

            c1 = c2

    except StopIteration:
        pass

    return graph


def load_trips(path, num_nodes):
    curr_origin = 0
    trips = np.zeros(shape=(num_nodes, num_nodes), dtype=np.float32)
    with open(path, 'r') as trips_file:
        metadata = True
        for line in trips_file:

            # Handle metadata at the beginning of the file
            if len(line) == 0:
                continue

            if line.startswith('Origin'):
                metadata = False
                tokens = list(filter(lambda x: len(x) > 0 and x != '\n', line.split(' ')))
                curr_origin = int(tokens[1]) - 1
                continue

            if metadata:
                continue

            tokens = list(filter(lambda x: len(x) > 0 and x != '\n' and x != ':', line.split(' ')))
            
            for i in range(0, len(tokens), 2):
                dest = int(tokens[i]) - 1
                amount = float(tokens[i+1].replace(';', ''))
                trips[curr_origin, dest] = amount

    return trips


def write(dataset, folder, index):
    """
    Serializes the given matrices  as sparse matrices in a set of files. We use a custom function
    here because the scipy.sparse.save_npz function only allows for a single sparse matrix.
    """
    source_data = {str(i): data[0] for i, data in enumerate(dataset)}
    sink_data = {str(i): data[1] for i, data in enumerate(dataset)}

    source_file_path = path.join(folder, 'source-demands-{0}.pkl.gz'.format(index))
    serialize_dict(dictionary=source_data, file_path=source_file_path)

    sink_file_path = path.join(folder, 'sink-demands-{0}.pkl.gz'.format(index))
    serialize_dict(dictionary=sink_data, file_path=sink_file_path)


def read(folder, file_index, sources, sinks, num_nodes):

    def read_dict(folder, name):
        file_path = path.join(folder, name)
        return deserialize_dict(file_path=file_path)

    source_data = read_dict(folder, 'source-demands-{0}.pkl.gz'.format(file_index))
    sink_data = read_dict(folder, 'sink-demands-{0}.pkl.gz'.format(file_index))

    dataset = []
    for i in range(len(source_data)):
        index = str(i)
        source_demands = source_data[index]
        sink_demands = sink_data[index]

        demands = np.zeros(shape=(num_nodes, 1))
        demands[sources, 0] = source_demands
        demands[sinks, 0] = sink_demands

        sp_mat = sp.csr_matrix(demands)
        dataset.append(sp_mat)


    return dataset


def load_embeddings(index_path, embedding_size, num_nodes):
    # Load Annoy index which stores the embedded vectors
    index = AnnoyIndex(embedding_size)
    index.load(index_path)

    embeddings = [index.get_item_vector(i) for i in range(num_nodes)]

    # Unload the index to save memory (loading mmaps the index file)
    index.unload()

    # V x D matrix of embeddings
    return np.array(embeddings)


def _parse_edge_features(edge_elems):
    init = int(_get_index_if_exists(edge_elems, 1, 0))
    term = int(_get_index_if_exists(edge_elems, 2, 0))

    features = {
        'capacity': float(_get_index_if_exists(edge_elems, 3, 0.0)),
        'length': float(_get_index_if_exists(edge_elems, 4, 0.0)),
        'free_flow_time': float(_get_index_if_exists(edge_elems, 5, 0.0)),
        'b': float(_get_index_if_exists(edge_elems, 6, 0.0)),
        'power': float(_get_index_if_exists(edge_elems, 7, 0.0)),
        'speed_limit': float(_get_index_if_exists(edge_elems, 8, 0.0)),
        'toll': float(_get_index_if_exists(edge_elems, 9, 0.0)),
        'link_type': int(_get_index_if_exists(edge_elems, 10, 0.0))
    }

    return init-1, term-1, features


def _get_index_if_exists(array, index, default):
    if len(array) <= index:
        return default
    if len(array[index]) == 0:
        return default
    return array[index]
