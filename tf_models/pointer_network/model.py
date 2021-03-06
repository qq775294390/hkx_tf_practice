import tensorflow as tf
from tensorflow.contrib.framework import arg_scope
from tensorflow.python.framework import tensor_util
from tensorflow.contrib import rnn
from tensorflow.python.util import nest
from tensorflow.python.framework import dtypes
# https://github.com/princewen/tensorflow_practice/blob/master/RL/myPtrNetwork/README.md

LSTMCell = rnn.LSTMCell
MultiRNNCell = rnn.MultiRNNCell

def trainable_initial_state(batch_size,
                            state_size,
                            initializer=None,
                            name="initial_state"):
    flat_state_size = nest.flatten(state_size)

    if not initializer:
        flat_initializer = tuple(tf.zeros_initializer for _ in flat_state_size)
    else:
        flat_initializer = tuple(tf.zeros_initializer for initializer in flat_state_size)

    names = ["{}_{}".format(name, i) for i in range(len(flat_state_size))]
    tiled_states = []

    for name, size, init in zip(names, flat_state_size, flat_initializer):
        shape_with_batch_dim = [1, size]
        initial_state_variable = tf.get_variable(
            name,
            shape=shape_with_batch_dim,
            initializer=init()
        )

        tiled_state = tf.tile(initial_state_variable,
                              [batch_size, 1], name=(name + "_tiled"))
        tiled_states.append(tiled_state)

    return nest.pack_sequence_as(structure=state_size,
                                 flat_sequence=tiled_states)

"""
sess.run( index_matrix_to_pairs(tf.convert_to_tensor([[-1,2,3],[3,5,6],[8,4,2]])))
array([[[ 0, -1],
        [ 0,  2],
        [ 0,  3]],

       [[ 1,  3],
        [ 1,  5],
        [ 1,  6]],

       [[ 2,  8],
        [ 2,  4],
        [ 2,  2]]])
"""
def index_matrix_to_pairs(index_matrix):
    # [[3,1,2],
    # [2,3,1]] -> [[[0, 3], [0, 1], [0, 2]],
    #              [[1, 2], [1, 3], [1, 1]]]
    replicated_first_indices = tf.range(tf.shape(index_matrix)[0])
    rank = len(index_matrix.get_shape())
    if rank == 2:
        # replicated_first_indices:array([[0, 0, 0],
        #                                 [1, 1, 1]])
        replicated_first_indices = tf.tile( # 复制元素
            tf.expand_dims(replicated_first_indices, axis=1),
            [1, tf.shape(index_matrix)[1]])
    return tf.stack([replicated_first_indices, index_matrix], axis=rank)




class Model(object):
    def __init__(self, config):

        self.task = config.task
        self.debug = config.debug
        self.config = config

        self.input_dim = config.input_dim
        self.hidden_dim = config.hidden_dim
        self.attention_dim = config.attention_dim
        self.num_layers = config.num_layers

        self.batch_size = config.batch_size

        self.max_enc_length = config.max_enc_length
        self.max_dec_length = config.max_dec_length
        self.num_glimpse = config.num_glimpse

        self.init_min_val = config.init_min_val
        self.init_max_val = config.init_max_val
        self.initializer = \
            tf.random_uniform_initializer(self.init_min_val, self.init_max_val) # 均匀分布

        self.lr_start = config.lr_start
        self.lr_decay_step = config.lr_decay_step
        self.lr_decay_rate = config.lr_decay_rate
        self.max_grad_norm = config.max_grad_norm

        self.debug_info = {}

        ##############
        # inputs
        ##############

        self.is_training = tf.placeholder_with_default(
            tf.constant(False, dtype=tf.bool),
            shape=(), name='is_training'
        )


        self._build_model()



    def _build_model(self):

        # -----------------定义输入------------------
        # enc_seq:[batch, max_enc_length]
        self.enc_seq = tf.placeholder(dtype=tf.float32,shape=[self.batch_size,self.max_enc_length,2], name='enc_seq')
        # target_seq:[batch, max_dec_length]
        self.target_seq = tf.placeholder(dtype=tf.int32,shape=[self.batch_size,self.max_dec_length], name='target_seq')
        # enc_seq_length:[batch]
        self.enc_seq_length = tf.placeholder(dtype=tf.int32,shape=[self.batch_size], name='enc_seq_length')
        # target_seq_length:[batch]
        self.target_seq_length = tf.placeholder(dtype=tf.int32,shape=[self.batch_size], name='target_seq_length')

        # ----------------输入处理-------------------
        # 将输入转换成embed
        # input_dim 是 2，hidden_dim 是 lstm的隐藏层的数量
        # input_embed:[1, input_dim=2, hidden_dim=256]
        input_embed = tf.get_variable( "input_embed",
            [1, self.input_dim, self.hidden_dim],
            initializer=self.initializer)

        """
        # 将 输入转换成embedding,一下是根据源码的转换过程：
        # enc_seq :[batch_size,seq_length,2] -> [batch_size,1,seq_length,2]，在第一维进行维数扩展, 2是x,y坐标, 看成NHWC
        # input_embed : [1,2,256] -> [1,1,2,256] # 在第0维进行维数扩展, 作为filters=[height=1,width=1, in_channel=2, out_channel=256]
        # 所以卷积后的输出为: [batch, 1, seq_length, out_channel]

        # tf.nn.conv1d首先将input和filter进行填充，然后进行二维卷积，因此卷积之后维度为batch * 1 * seq_length * 256
        # 最后还有一步squeeze的操作，从tensor中删除所有大小是1的维度，所以最后的维数为batch * seq_length * 256
        # 即将输入数据:[batch, seq_length, input_dim=2] -> 高维[batch, seq_length, hidden_dim=256], 其实就相当于最后一个维度全连接而己
        # 最后还有一步squeeze的操作，从tensor中删除所有大小是1的维度，所以最后的维数为batch * seq_length * 256
        # embeded_enc_inputs: [batch, seq_length, hidden_dim=256]
        
        问题是要实现这种变换,为何不用全连接呢?我没想明白
        全连接参数:input_dim*hidden_dim 
        卷积参数:1*1*in_channel*out_channel= input_dim*hidden_dim
        """
        self.embeded_enc_inputs = tf.nn.conv1d(self.enc_seq, input_embed, 1, "VALID")

        # -----------------encoder------------------
        tf.logging.info("Create a model..")
        with tf.variable_scope("encoder"):
            # 构建一个多层的LSTM
            self.enc_cell = LSTMCell(
                self.hidden_dim,
                initializer=self.initializer)

            if self.num_layers > 1:
                cells = [self.enc_cell] * self.num_layers
                self.enc_cell = MultiRNNCell(cells)
            # 建立可训练的lstm初始状态
            self.enc_init_state = trainable_initial_state(self.batch_size, self.enc_cell.state_size)

            # embeded_enc_inputs: [batch, seq_length, hidden_dim=256],这里的seq_length其实已经padding到max_sequence长度
            # self.encoder_outputs : [batch_size, max_sequence, hidden_dim]
            self.enc_outputs, self.enc_final_states = tf.nn.dynamic_rnn(
                self.enc_cell, # lstm cell
                self.embeded_enc_inputs, # [batch, seq_length, hidden_dim=256]
                self.enc_seq_length, # [batch]
                self.enc_init_state)

            # 给最开头添加一个结束标记，同时这个标记也将作为decoder的初始输入
            # batch_size * 1 * hidden_dim
            self.first_decoder_input = tf.expand_dims(trainable_initial_state(
                self.batch_size, self.hidden_dim, name="first_decoder_input"),
                axis=1)
            # batch_size * (max_sequence + 1) * hidden_dim
            self.enc_outputs = tf.concat([self.first_decoder_input, self.enc_outputs], axis=1)

        # -----------------decoder 训练--------------------
        """
        与seq2seq不同的是，pointer-network的输入并不是target序列的embedding，而是根据target序列的值选择相应位置的encoder的输出。
        我们知道encoder的输出长度在添加了开始输出之后形状为[batch ,max_enc_seq_length + 1]。现在假设我们拿第一条记录进行训练，第一条记录的预测序列是[1,2,4]，那么decoder依次的输入是
        self.enc_outputs[0][0], self.enc_outputs[0][1],self.enc_outputs[0][2],self.enc_outputs[0][4]，那么如何根据target序列来选择encoder的输出呢，这里就要用到我们刚刚定义的index_matrix_to_pairs函数以及gather_nd函数：
        """
        with tf.variable_scope("decoder"):
            # target_seq:
            # [[3,1,2], 第一个样本的目标预测序列
            #  [2,3,1]] 第二个样本的目标预测序列
            # ->
            # target_idx_pairs:
            # [[[0, 3], [0, 1], [0, 2]], 第一个样本的目标预测序列
            #  [[1, 2], [1, 3], [1, 1]]]
            # 将target_index转化为 (batch_index, target_index) 对
            # target_seq: [batch, max_dec_length=10]
            # target_idx_pairs: [batch=20, max_dec_length=10, 2]
            self.target_idx_pairs = index_matrix_to_pairs(self.target_seq)
            # enc_outputs: [batch, max_enc_sequence + 1, hidden_dim]
            # target_idx_pairs: [batch, max_dec_length, 2]
            # embeded_dec_inputs: [batch, max_dec_length, hidden_dim]
            # garther_nd完成的功能就是从enc_outputs中选出target_seq中对应的index的向量,组成embed_dec_inputs
            self.embeded_dec_inputs = tf.stop_gradient(
                tf.gather_nd(self.enc_outputs, self.target_idx_pairs))
            """
            在此处,我们可以看到ptr-net与MT中的seq2seq区别,seq2seq中的decoder输入一般是另外的embedding输入,
            而在ptr-net中,直接将target_seq_ids所对应的encoder的hidden输出copy过来当作decoder的inputs!
            然后用这些decoder中的inputs来预测target_seq_ids(softmax选最大值)

            因此,此处不能有梯度回传
            """
            self.debug_info["target_seq"] = self.target_seq
            self.debug_info["enc_outputs"] = self.enc_outputs
            self.debug_info["target_idx_pairs"] = self.target_idx_pairs
            self.debug_info["embeded_dec_inputs"] = self.embeded_dec_inputs
            # 给target最后一维增加结束标记,数据都是从1开始的，所以结束也是回到1
            # tiled_zero_idxs:[batch, 1],注意,补1的地方的值均为0
            tiled_zero_idxs = tf.tile(tf.zeros([1, 1], dtype=tf.int32), [self.batch_size, 1], name="tiled_zero_idxs")
            self.add_terminal_target_seq = tf.concat([self.target_seq, tiled_zero_idxs], axis=1) # [batch, max_dec_length+1], 注意:target id中结束标记加在末尾
            #如果使用了结束标记的话，要给encoder的输出拼上开始状态，同时给decoder的输入拼上开始状态
            # embeded_dec_inputs: [batch, 1+max_dec_length, hidden_dim]
            self.embeded_dec_inputs = tf.concat([self.first_decoder_input, self.embeded_dec_inputs], axis=1) # embedding中结束标记加在首位
            """
            从以上可以看出, target_id中结束标记加在末尾,而embedding中结束标记加在首位,相当于embedding(i) => target(i+1)
            """

            # 建立一个多层的lstm网络
            self.dec_cell = LSTMCell(
                self.hidden_dim,
                initializer=self.initializer)

            if self.num_layers > 1:
                cells = [self.dec_cell] * self.num_layers
                self.dec_cell = MultiRNNCell(cells)

            # encoder的最后的状态作为decoder的初始状态
            dec_state = self.enc_final_states

            # 预测的序列
            self.predict_indexes = []
            # 预测的softmax序列，用于计算损失
            self.predict_indexes_distribution = []

            """
            对于decoder来说，这里我们每次每个batch只输入一个值，然后使用循环来实现整个decoder的过程：
            """
            # 训练self.max_dec_length  + 1轮，每一轮输入batch * hiddennum
            for i in range(self.max_dec_length  + 1):
                if i > 0:
                    tf.get_variable_scope().reuse_variables()
                cell_input = tf.squeeze(self.embeded_dec_inputs[:, i, :])  # [batch,hidden]
                output_i, dec_state = self.dec_cell(cell_input, dec_state)  # 经过一个时间步的lstm后的output: [batch, hidden],由于并没有多个timestep,所以不需要dynamic_rnn
                # enc_outputs: [batch_size , (max_sequence + 1) , hidden_dim]
                # 使用pointer机制选择得到softmax的输出，idx_softmax:[batch, max_enc_length + 1]
                # 论文中decoder时刻i: u(i,j) = V^T*tanh(W1*ej+W2*di), j in(1,...,n)
                idx_softmax = self.choose_index(self.enc_outputs, output_i) #idx_softmax:[batch, max_enc_length + 1]
                # 选择每个batch 最大的id, [batch]
                idx = tf.argmax(idx_softmax, axis=1, output_type=dtypes.int32)
                # decoder的每个输出的softmax序列
                self.predict_indexes_distribution.append(idx_softmax)  # [max_dec_length+1, batch, max_enc_length + 1]
                # decoder的每个输出的id
                self.predict_indexes.append(idx) # [max_dec_length+1, batch]
                """
                即对于每条样本的每个输出时刻i,都需要计算与每个输入时刻j的attention,因此时间复杂度为o(m*n),运行速度较慢
                """

            self.predict_indexes = tf.convert_to_tensor(self.predict_indexes) # list-> tensor, [max_dec_length+1, batch]
            self.predict_indexes_distribution = tf.convert_to_tensor(self.predict_indexes_distribution) # [max_dec_length+1, batch, max_enc_length + 1]

        # ----------------loss------------------
        with tf.variable_scope("loss"):
            # # 我们计算交叉熵来作为我们的损失
            # # -sum(y * log y')
            # # 首先我们要对我们的输出进行一定的处理，首先我们的target的维度是batch * self.max_dec_length * 1，
            # # 而训练或预测得到的softmax序列是 self.max_dec_length +1 * batch * self.max_enc_length + 1
            # # 所以我们先去掉预测序列的最后一行，然后进行transpose，再转成一行
            # # 对实际的序列，我们先将其转换成one-hot，再转成一行，随后便可以计算损失
            #
            # self.dec_pred_logits = tf.reshape(
            #     tf.transpose(tf.squeeze(self.predict_indexes_distribution), [1, 0, 2]), [-1])  # B * D * E + 1
            # self.dec_inference_logits = tf.reshape(
            #     tf.transpose(tf.squeeze(self.infer_predict_indexes_distribution), [1, 0, 2]),
            #     [-1])  # B * D * E + 1
            # self.dec_target_labels = tf.reshape(tf.one_hot(self.add_terminal_target_seq, depth=self.max_enc_length+ 1), [-1])
            #
            # self.loss = -tf.reduce_sum(self.dec_target_labels * tf.log(self.dec_pred_logits))
            # self.inference_loss = -tf.reduce_mean(self.dec_target_labels * tf.log(self.dec_inference_logits))
            #

            # predict_indexes_distribution: [max_dec_length+1, batch, max_enc_length + 1]
            # training_logits: [max_dec_length, batch, max_enc_length + 1] -> [batch, max_dec_length, max_enc_length + 1]
            training_logits = tf.identity(tf.transpose(self.predict_indexes_distribution[:-1],[1,0,2])) # [:-1],去掉最后的结束符
            # target_seq:[batch, max_dec_length]
            targets = tf.identity(self.target_seq)
            # target_seq_length:[batch]
            # max_dec_length:[1]
            # masks: [batch, max_dec_length], 用sequence_mask补零
            masks = tf.sequence_mask(self.target_seq_length,self.max_dec_length,dtype=tf.float32,name="masks")
            # training_logits: [batch, max_dec_length, max_enc_length + 1]
            # loss:[1],此处的loss为所有序列拼起来的平均值
            self.loss = tf.contrib.seq2seq.sequence_loss(
                logits=training_logits, # [batch_size, sequence_length, num_decoder_symbols]
                targets=targets, # [batch_size, sequence_length]
                weights=masks # [batch_size, sequence_length]
            )
            self.optimizer = tf.train.AdamOptimizer(self.lr_start)
            self.train_op = self.optimizer.minimize(self.loss)

        # ----------------------decoder inference----------------------
        # 预测输出的id序列
        self.infer_predict_indexes = []
        # 预测输出的softmax序列
        self.infer_predict_indexes_distribution = []
        with tf.variable_scope("decoder", reuse=True):
            dec_state = self.enc_final_states
            # 预测阶段最开始的输入是之前定义的初始输入
            self.predict_decoder_input = self.first_decoder_input
            for i in range(self.max_dec_length + 1):
                if i > 0:
                    tf.get_variable_scope().reuse_variables()

                """
                self.embeded_dec_inputs = tf.concat([self.first_decoder_input, self.embeded_dec_inputs], axis=1) # embedding中结束标记加在首位
                注意:此处predict_decoder_input与embed_dec_inputs不同,此处并没有target_id组成的序列的输入
                """
                self.predict_decoder_input = tf.squeeze(self.predict_decoder_input)  # [batch, 1, hidden] -> [batch, hidden]
                # 因为这里是按时间time展开的,所以output里没有timestep
                output_i, dec_state = self.dec_cell(self.predict_decoder_input, dec_state)  # output:[batch, hidden]
                # 同样根据pointer机制得到softmax输出
                idx_softmax = self.choose_index(self.enc_outputs, output_i)  # [batch, enc_max_length+1]
                # 选择 最大的那个id
                idx = tf.argmax(idx_softmax, axis=1, output_type=dtypes.int32)  # [batch]
                # 将选择的id转换为pair
                idx_pairs = index_matrix_to_pairs(idx)
                # 选择的下一个时刻的输入,此处亦与train decoder阶段不同
                self.predict_decoder_input = tf.stop_gradient(tf.gather_nd(self.enc_outputs, idx_pairs))  # [batch,1, hidden]

                # decoder的每个输出的id
                self.infer_predict_indexes.append(idx) # [max_dec_length+1, batch]
                # decoder的每个输出的softmax序列
                self.infer_predict_indexes_distribution.append(idx_softmax) # [max_dec_length+1, batch, enc_max_length+1]
            self.infer_predict_indexes = tf.convert_to_tensor(self.infer_predict_indexes, dtype=tf.int32)
            self.infer_predict_indexes_distribution = tf.convert_to_tensor(self.infer_predict_indexes_distribution, dtype=tf.float32)



    def train(self, sess, batch):
        #对于训练阶段，需要执行self.train_op, self.loss, self.summary_op三个op，并传入相应的数据
        feed_dict = {self.enc_seq: batch['enc_seq'],
                     self.enc_seq_length: batch['enc_seq_length'],
                     self.target_seq: batch['target_seq'],
                     self.target_seq_length: batch['target_seq_length']}
        debug_info = ""
        if self.config.debug:
            _, loss, debug_info = sess.run([self.train_op, self.loss, self.debug_info], feed_dict=feed_dict)
        else:
            _, loss = sess.run([self.train_op, self.loss], feed_dict=feed_dict)
        return loss, debug_info

    def eval(self, sess, batch):
        # 对于eval阶段，不需要反向传播，所以只执行self.loss, self.summary_op两个op，并传入相应的数据
        feed_dict = {self.enc_seq: batch['enc_seq'],
                      self.enc_seq_length: batch['enc_seq_length'],
                      self.target_seq: batch['target_seq'],
                      self.target_seq_length: batch['target_seq_length']}
        loss= sess.run([self.loss], feed_dict=feed_dict)
        return loss

    def infer(self, sess, batch):

        feed_dict = {self.enc_seq: batch['enc_seq'],
                     self.enc_seq_length: batch['enc_seq_length'],
                     self.target_seq: batch['target_seq'],
                     self.target_seq_length: batch['target_seq_length']}
        predict = sess.run([self.infer_predict_indexes], feed_dict=feed_dict)
        return predict


    def attention(self, ref_encoders, query, with_softmax, scope="attention"):
        """

        :param ref_encoders: encoder的输出, [batch, max_enc_length, hidden]
        :param query: decoder的输入, [batch, hidden]
        :param with_softmax:
        :param scope:
        :return: [batch, max_enc_length]
        """
        with tf.variable_scope(scope):
            W_encoder = tf.get_variable("W_e", [self.hidden_dim, self.attention_dim], initializer=self.initializer)  # [hidden, atten_dim]
            W_decoder = tf.get_variable("W_d", [self.hidden_dim, self.attention_dim], initializer=self.initializer) # [hidden, atten_dim]
            # query: [batch, hidden]
            decoder_portion = tf.matmul(query, W_decoder) # dec_portion: [batch, atten_dim=20]

            scores = [] # [ max_enc_length+1, batch]
            v_blend = tf.get_variable("v_blend", [self.attention_dim, 1], initializer=self.initializer)  # v_blend:[atten_dim,1]
            bais_blend = tf.get_variable("bais_v_blend", [1], initializer=self.initializer)  # bais_blend:1
            # 对于输出的每个时刻t
            for i in range(self.max_enc_length + 1):
                # ref:[batch, max_enc_length, hidden], w1:[hidden, atten_dim]
                refi = tf.matmul(tf.squeeze(ref_encoders[:, i, :]), W_encoder) # refi: [batch, atten_dim],我感觉这里存在这很多重复计算
                # V^T*tanh(W_decoder*query + W_encoder* encoder_hidden_i) + bias
                ui = tf.add(tf.matmul(tf.nn.tanh(decoder_portion + refi), v_blend), bais_blend) # [batch, 1]
                scores.append(tf.squeeze(ui)) # [max_enc_length+1, batch]

            scores = tf.transpose(scores, [1,0]) # [batch, max_enc_length+1]
            if with_softmax:
                return tf.nn.softmax(scores, axis=1) # [batch, max_enc_length+1]
            else:
                return scores # [batch, max_enc_length+1]
    """ 
        ref: [batch, max_enc_encoder, hidden]
        query:[batch, hidden]
        计算经过与输入对齐之后的query
    """
    def glimpse_fn(self, ref, query, scope="glimpse"):
        # p:[batch, max_enc_encoder], 每行均为一个概率分布
        p = self.attention(ref, query, with_softmax=True, scope=scope)
        # alignments: [batch, max_enc_encoder, 1]
        alignments = tf.expand_dims(p, axis=2)
        return tf.reduce_sum(alignments * ref, axis=[1], keep_dims=False) # [batch, hidden]

    """ 
        ref: [batch, max_enc_encoder, hidden]
        query:[batch, hidden]
    """
    def choose_index(self,ref,query):
        if self.num_glimpse > 0:
            query = self.glimpse_fn(ref,query)
        return self.attention(ref, query, with_softmax=True, scope="attention")