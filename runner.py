import argparse
import networkx as nx
import numpy as np
from utils import load_params, restore_params
from utils import append_row_to_log, create_demands 
from load import load_to_networkx, load_embeddings
from load import write_dataset
from constants import *
from sparse_mcf import SparseMCF
from mcf import MCF


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Computing Min Cost Flows using Graph Neural Networks.')
    parser.add_argument('--params', type=str, help='Parameters JSON file.')
    parser.add_argument('--train', action='store_true', help='Flag to specify training.')
    parser.add_argument('--generate', action='store_true', help='Flag to specify dataset generation.')
    parser.add_argument('--test', action='store_true', help='Flag to specify testing.')
    parser.add_argument('--model', type=str, help='Path to trained model.')
    args = parser.parse_args()

    # Fetch parameters
    if args.params is not None:
        params = load_params(args.params)
    else:
        # Load parameters used to create the given model
        params = restore_params(args.model)
    
    mcf_solver = MCF(params=params)

    if args.train:
        mcf_solver.train()
    elif args.generate:
        generate(params)
    elif args.test:
        mcf_solver.test(args.model)


def generate(params):

    # Load graph
    graph_path = 'graphs/{0}.tntp'.format(params['graph_name'])
    graph = load_to_networkx(path=graph_path)

    train_file = 'datasets/{0}_train.txt'.format(params['dataset_name'])
    valid_file = 'datasets/{0}_valid.txt'.format(params['dataset_name'])
    test_file = 'datasets/{0}_test.txt'.format(params['dataset_name'])

    file_paths = [train_file, valid_file, test_file]
    samples = [params['train_samples'], params['valid_samples'], params['test_samples']]
    for file_path, num_samples in zip(file_paths, samples):
        dataset = []
        for i in range(num_samples):
            d = create_demands(graph=graph,
                               min_max_sources=params['min_max_sources'],
                               min_max_sinks=params['min_max_sinks'])
            dataset.append(d)
            if len(dataset) == WRITE_THRESHOLD:
                write_dataset(dataset, file_path)
                print('Wrote {0} samples to {1}'.format(i, file_path))
                dataset = []

        # Clean up
        if len(dataset) > 0:
            write_dataset(dataset, file_path)


if __name__ == '__main__':
    main()
