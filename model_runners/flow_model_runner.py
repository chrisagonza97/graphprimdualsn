import numpy as np
import tensorflow as tf
from utils.graph_utils import pad_adj_list, adj_matrix_to_list
from core.dataset import DatasetManager, Series
from model_runners.model_runner import ModelRunner
from models.flow_model import FlowModel


class FlowModelRunner(ModelRunner):

    def create_placeholders(self, model, **kwargs):

        # Model parameters
        b = self.params['batch_size']
        num_neighborhoods = self.params['num_neighborhoods']

        embedding_size = kwargs['embedding_size']
        max_num_nodes = kwargs['max_num_nodes'] + 1
        max_degree = kwargs['max_degree']
        max_out_neighborhood_degrees = kwargs['max_out_neighborhood_degrees']
        max_in_neighborhood_degrees = kwargs['max_in_neighborhood_degrees']

        # Placeholder shapes
        node_shape = [b, max_num_nodes, self.num_node_features]
        demands_shape = [b, max_num_nodes, 1]
        adj_shape = [b, max_num_nodes, max_degree]
        common_neighbors_shape = [b, max_num_nodes, 2 * max_degree]
        embedding_shape = [b, max_num_nodes, embedding_size]
        num_nodes_shape = [b, 1]

        node_ph = model.create_placeholder(dtype=tf.float32,
                                           shape=node_shape,
                                           name='node-ph',
                                           is_sparse=False)
        demands_ph = model.create_placeholder(dtype=tf.float32,
                                              shape=demands_shape,
                                              name='demands-ph',
                                              is_sparse=False)
        adj_ph = model.create_placeholder(dtype=tf.int32,
                                          shape=adj_shape,
                                          name='adj-ph',
                                          is_sparse=False)
        inv_adj_ph = model.create_placeholder(dtype=tf.int32,
                                              shape=adj_shape,
                                              name='inv-adj-ph',
                                              is_sparse=False)
        in_indices_ph = model.create_placeholder(dtype=tf.int32,
                                                 shape=[np.prod(adj_shape), 3],
                                                 name='in-indices-ph',
                                                 is_sparse=False)
        rev_indices_ph = model.create_placeholder(dtype=tf.int32,
                                                  shape=[np.prod(adj_shape), 3],
                                                  name='rev-indices-ph',
                                                  is_sparse=False)
        node_embedding_ph = model.create_placeholder(dtype=tf.float32,
                                                     shape=embedding_shape,
                                                     name='node-embedding-ph',
                                                     is_sparse=False)
        dropout_keep_ph = model.create_placeholder(dtype=tf.float32,
                                                   shape=(),
                                                   name='dropout-keep-ph',
                                                   is_sparse=False)
        num_nodes_ph = model.create_placeholder(dtype=tf.int32,
                                                shape=num_nodes_shape,
                                                name='num-nodes-ph',
                                                is_sparse=False)
        edge_lengths_ph = model.create_placeholder(dtype=tf.float32,
                                                   shape=adj_shape,
                                                   name='edge-lengths-ph',
                                                   is_sparse=False)
        normalized_edge_lengths_ph = model.create_placeholder(dtype=tf.float32,
                                                              shape=adj_shape,
                                                              name='norm-edge-lengths-ph',
                                                              is_sparse=False)
        true_costs_ph = model.create_placeholder(dtype=tf.float32,
                                                 shape=(b,),
                                                 name='true-costs-ph',
                                                 is_sparse=False)

        out_neighborhood_phs = []
        in_neighborhood_phs = []
        for i in range(num_neighborhoods + 1):
            out_shape = [b, max_num_nodes, max_out_neighborhood_degrees[i]]
            out_ph = model.create_placeholder(dtype=tf.int32,
                                              shape=out_shape,
                                              name='out-neighborhood-{0}-ph'.format(i),
                                              is_sparse=False)

            in_shape = [b, max_num_nodes, max_in_neighborhood_degrees[i]]
            in_ph = model.create_placeholder(dtype=tf.int32,
                                             shape=in_shape,
                                             name='in-neighborhood-{0}-ph'.format(i),
                                             is_sparse=False)

            out_neighborhood_phs.append(out_ph)
            in_neighborhood_phs.append(in_ph)

        return {
            'node_features': node_ph,
            'demands': demands_ph,
            'adj_lst': adj_ph,
            'inv_adj_lst': inv_adj_ph,
            'out_neighborhoods': out_neighborhood_phs,
            'in_neighborhoods': in_neighborhood_phs,
            'in_indices': in_indices_ph,
            'rev_indices': rev_indices_ph,
            'dropout_keep_prob': dropout_keep_ph,
            'edge_lengths': edge_lengths_ph,
            'norm_edge_lengths': normalized_edge_lengths_ph,
            'num_nodes': num_nodes_ph,
            'max_num_nodes': max_num_nodes,
            'true_costs': true_costs_ph
        }

    def create_feed_dict(self, placeholders, batch, batch_size, data_series, **kwargs):

        # Padding parameters
        max_degree = kwargs['max_degree']
        max_num_nodes = kwargs['max_num_nodes']
        max_out_neighborhood_degrees = kwargs['max_out_neighborhood_degrees']
        max_in_neighborhood_degrees = kwargs['max_in_neighborhood_degrees']

        # Fetch features for each sample in the given batch
        node_features = np.array([sample.node_features for sample in batch])
        demands = np.array([sample.demands for sample in batch])
        adj_lsts = np.array([sample.adj_lst for sample in batch])
        inv_adj_lsts = np.array([sample.inv_adj_lst for sample in batch])
        num_nodes = np.array([sample.num_nodes for sample in batch])
        edge_lengths = np.array([sample.edge_lengths for sample in batch])
        norm_edge_lengths = np.array([sample.normalized_edge_lengths for sample in batch])
        dropout_keep = self.params['dropout_keep_prob'] if data_series == Series.TRAIN else 1.0
        true_costs = np.array([sample.true_cost for sample in batch])

        # 3D indexing used for flow computation and correction
        batch_indices = np.arange(start=0, stop=batch_size)
        batch_indices = np.repeat(batch_indices, adj_lsts.shape[1] * max_degree).reshape((-1, 1))

        rev_indices = np.vstack([sample.rev_indices for sample in batch])
        rev_indices = np.concatenate([batch_indices, rev_indices], axis=1)

        in_indices = np.vstack([sample.in_indices for sample in batch])
        in_indices = np.concatenate([batch_indices, in_indices], axis=1)

        # Add dummy embeddings, features and demands to account for added node
        demands = np.insert(demands, demands.shape[1], 0, axis=1)
        node_features = np.insert(node_features, node_features.shape[1], 0, axis=1)

        feed_dict = {
            placeholders['node_features']: node_features,
            placeholders['demands']: demands,
            placeholders['adj_lst']: adj_lsts,
            placeholders['inv_adj_lst']: inv_adj_lsts,
            placeholders['edge_lengths']: edge_lengths,
            placeholders['norm_edge_lengths']: norm_edge_lengths,
            placeholders['dropout_keep_prob']: dropout_keep,
            placeholders['num_nodes']: np.reshape(num_nodes, [-1, 1]),
            placeholders['in_indices']: in_indices,
            placeholders['rev_indices']: rev_indices,
            placeholders['true_costs']: true_costs
        }

        for i in range(self.params['num_neighborhoods'] + 1):
            out_neighborhood = [sample.out_neighborhoods[i] for sample in batch]
            in_neighborhood = [sample.in_neighborhoods[i] for sample in batch]

            out_ph = placeholders['out_neighborhoods'][i]
            feed_dict[out_ph] = out_neighborhood

            in_ph = placeholders['in_neighborhoods'][i]
            feed_dict[in_ph] = in_neighborhood

        return feed_dict

    def create_model(self, params):
        return FlowModel(params=params)
