import tensorflow as tf
from models.base_model import Model
from core.layers import MLP, DirectionalGAT, GRU, SparseMax
from utils.constants import BIG_NUMBER, SMALL_NUMBER
from utils.tf_utils import masked_gather
from utils.flow_utils import mcf_solver, dual_flow, destination_attn
from cost_functions.cost_functions import get_cost_function


class DirectionalModel(Model):

    def __init__(self, params, name='directional-model'):
        super(DirectionalModel, self).__init__(params, name)
        self.cost_fn = get_cost_function(cost_fn=params['cost_fn'])
        self.use_edge_lengths = params['cost_fn']['use_edges']

    def build(self, **kwargs):

        # B x V x 1 tensor which contains node demands
        demands = kwargs['demands']

        # B x V x F tensor which contains node features
        node_features = kwargs['node_features']

        # B x V x D tensor containing the padded adjacency list
        adj_lst = kwargs['adj_lst']

        # B x V x D tensor containing padded inverse adjacency list
        inv_adj_lst = kwargs['inv_adj_lst']

        # B x V x D tensor with edge lengths
        edge_lengths = kwargs['edge_lengths']

        # B x V x D tensor with normalized edge lengths
        norm_edge_lengths = kwargs['norm_edge_lengths']

        # B*V*D x 3 tensor containing 3D indices used to compute inflow
        in_indices = kwargs['in_indices']

        # B*V*D x 3 tensor containing 3D indices of outgoing neighbors
        rev_indices = kwargs['rev_indices']

        # B*V*D x 3 tensor containing 3D indices of opposite edges
        opp_indices = kwargs['opp_indices']

        # B x 1
        num_nodes = kwargs['num_nodes']

        # Floating point number between 0 and 1
        dropout_keep_prob = kwargs['dropout_keep_prob']

        # Scalar Int
        max_num_nodes = kwargs['max_num_nodes']

        with self._sess.graph.as_default():
            with tf.variable_scope(self.name, reuse=tf.AUTO_REUSE):

                node_indices = tf.range(start=0, limit=max_num_nodes)
                node_indices = tf.tile(tf.expand_dims(node_indices, axis=0),
                                       multiples=(tf.shape(num_nodes)[0], 1))

                node_embedding_init = tf.random.normal(shape=(max_num_nodes, self.params['node_embedding_size']))
                node_embedding_var = tf.Variable(node_embedding_init,
                                                 trainable=True,
                                                 name='node-embedding-var')
                node_embeddings = tf.nn.embedding_lookup(params=node_embedding_var,
                                                         ids=node_indices,
                                                         max_norm=1,
                                                         name='node-embedding-lookup')
                init_features = tf.concat([node_embeddings, node_features], axis=-1)

                # Node encoding, B x V x D
                encoder = MLP(hidden_sizes=[],
                              output_size=self.params['node_encoding'],
                              activation=None,
                              activate_final=True,
                              name='node-encoder')
                node_encoding = encoder(inputs=init_features, dropout_keep_prob=dropout_keep_prob)

                # Initial node encodings for each neighbor, B x V x D x F tensor
                initial_encoding, _ = masked_gather(values=node_encoding,
                                                    indices=adj_lst,
                                                    mask_index=num_nodes,
                                                    set_zero=True)

                # Layers used for node aggregation and update
                node_agg = DirectionalGAT(output_size=self.params['node_encoding'],
                                          activation=tf.nn.tanh,
                                          name='directional-gat')

                node_gru = GRU(output_size=self.params['node_encoding'],
                               activation=tf.nn.tanh,
                               name='node-gru')

                # Mask to remove nonexistent edges, B x V x D
                mask_indices = tf.expand_dims(num_nodes, axis=-1)
                mask = tf.cast(tf.equal(adj_lst, mask_indices), tf.float32)

                # B x V x D x F tensor containing directional state representations
                node_encoding = initial_encoding

                edge_features = norm_edge_lengths if self.use_edge_lengths else None

                # Combine message passing steps
                for _ in range(self.params['graph_layers']):
                    next_encoding = node_agg(inputs=node_encoding,
                                             adj_lst=adj_lst,
                                             mask_index=num_nodes,
                                             mask=mask,
                                             edge_lengths=edge_features,
                                             initial_states=initial_encoding,
                                             weight_dropout_keep=dropout_keep_prob,
                                             attn_dropout_keep=dropout_keep_prob)

                    node_encoding = node_gru(inputs=next_encoding,
                                             state=node_encoding,
                                             dropout_keep_prob=dropout_keep_prob)

                # Compute flow proportions
                decoder = MLP(hidden_sizes=self.params['decoder_hidden'],
                              output_size=1,
                              activation=tf.nn.tanh,
                              activate_final=False,
                              name='node-decoder')

                # B x V x D x 1
                opp_encoding = tf.reshape(tf.gather_nd(node_encoding, opp_indices),
                                          tf.shape(node_encoding))
                comb_encoding = node_encoding + opp_encoding
                node_weights = decoder(inputs=tf.concat([node_encoding, comb_encoding], axis=-1))

                # B x V x D
                node_weights = tf.squeeze(node_weights, axis=-1)

                # B x V x D, node weights augmented by destinations
                inv_mask = tf.cast(tf.equal(inv_adj_lst, mask_indices), tf.float32)
                node_weights = destination_attn(node_weights=node_weights,
                                                in_indices=in_indices,
                                                rev_indices=rev_indices,
                                                mask=inv_mask)

                # Mask out nonexistent neighbors before normalization, B x V x D
                pred_weights = (-BIG_NUMBER * mask) + node_weights

                # Normalize weights for outgoing neighbors
                if self.params['use_sparsemax']:
                    sparsemax = SparseMax(epsilon=1e-5)
                    normalized_weights = sparsemax(inputs=pred_weights, mask=(1.0 - mask))
                else:
                    normalized_weights = tf.nn.softmax(pred_weights, axis=-1)

                flow, pflow = mcf_solver(pred_weights=normalized_weights,
                                         demand=demands,
                                         in_indices=in_indices,
                                         max_iters=self.params['flow_iters'])

                cost_tensor = self.cost_fn.apply(flow, edge_lengths) if self.use_edge_lengths else self.cost_fn.apply(flow)
                flow_cost = tf.reduce_sum(cost_tensor, axis=[1, 2])

                # Sum all directional states to get the final node state to compute dual variables
                # This tensor is B x V x F
                node_states = tf.reduce_sum(node_encoding, axis=-2)

                # Compute dual variables
                dual_decoder = MLP(hidden_sizes=self.params['decoder_hidden'],
                                   output_size=1,
                                   activation=tf.nn.tanh,
                                   activate_final=False,
                                   name='dual-decoder')

                # Apply a ReLU because we know the cost is always nonnegative
                dual_vars = tf.nn.relu(dual_decoder(inputs=node_states))

                # B x (V + 1) x D tensor of repeated dual variables
                dual = mask * dual_vars

                # Need to compute transpose (via a masked gather)
                dual_tr, _ = masked_gather(values=dual_vars,
                                           indices=adj_lst,
                                           mask_index=num_nodes,
                                           set_zero=True)
                dual_tr = tf.squeeze(dual_tr, axis=-1)

                # alpha_j - alpha_i
                dual_diff = dual_tr - dual

                # B x V x D
                dual_flows = dual_flow(dual_diff=dual_diff,
                                       adj_mask=mask,
                                       cost_fn=self.cost_fn,
                                       edge_lengths=edge_lengths,
                                       should_use_edges=self.use_edge_lengths,
                                       step_size=self.params['dual_step_size'],
                                       momentum=self.params['dual_momentum'],
                                       max_iters=self.params['dual_iters'])

                dual_demand = tf.reduce_sum(dual_vars * demands, axis=[1, 2])

                if self.use_edge_lengths:
                    dual_flow_cost = self.cost_fn.apply(dual_flows, edge_lengths)
                else:
                    dual_flow_cost = self.cost_fn.apply(dual_flows)

                dual_flow_cost += dual_diff * dual_flows
                dual_cost = tf.reduce_sum(dual_flow_cost, axis=[1, 2]) - dual_demand

                self.loss = flow_cost - dual_cost
                self.loss_op = tf.reduce_mean(self.loss)

                # Named outputs
                self.output_ops['flow'] = flow
                self.output_ops['flow_cost'] = flow_cost
                self.output_ops['normalized_weights'] = normalized_weights
                self.output_ops['dual_cost'] = dual_cost
                self.output_ops['pred_weights'] = pred_weights

                self.optimizer_op = self._build_optimizer_op()
