# coding=utf-8
from datetime import datetime
import os,sys
import time

import tensorflow as tf
import mnist_inference

# 定义训练神经网络时需要用到的参数。
BATCH_SIZE = 100 
LEARNING_RATE_BASE = 0.001
LEARNING_RATE_DECAY = 0.99
REGULARAZTION_RATE = 0.0001
TRAINING_STEPS = 1000
MOVING_AVERAGE_DECAY = 0.99 
N_GPU = 4

# 定义日志和模型输出的路径。
MODEL_SAVE_PATH = "logs_and_models/"
MODEL_NAME = "model.ckpt"
DATA_PATH = "data/train.tfrecords"

# 定义输入队列得到训练数据，具体细节可以参考第七章。
def get_input():
    filename_queue = tf.train.string_input_producer([DATA_PATH]) 
    reader = tf.TFRecordReader()
    _, serialized_example = reader.read(filename_queue)

    # 定义数据解析格式。
    features = tf.parse_single_example(
        serialized_example,
        features={
            'image_raw': tf.FixedLenFeature([], tf.string),
            'pixels': tf.FixedLenFeature([], tf.int64),
            'label': tf.FixedLenFeature([], tf.int64),
        })

    # 解析图片和标签信息。
    decoded_image = tf.decode_raw(features['image_raw'], tf.uint8)
    reshaped_image = tf.reshape(decoded_image, [784])
    retyped_image = tf.cast(reshaped_image, tf.float32)
    label = tf.cast(features['label'], tf.int32)
    
    # 定义输入队列并返回。
    min_after_dequeue = 10000
    capacity = min_after_dequeue + 3 * BATCH_SIZE
    return tf.train.shuffle_batch(
        [retyped_image, label],
        batch_size=BATCH_SIZE,
        capacity=capacity,
        min_after_dequeue=min_after_dequeue)

# 定义损失函数。
def get_loss(x, y_, regularizer, scope, reuse_variables=None):
    print("variable_scope:",tf.get_variable_scope())
    with tf.variable_scope(tf.get_variable_scope(), reuse=reuse_variables):
        y = mnist_inference.inference(x, regularizer)
    cross_entropy = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(logits=y, labels=y_))
    regularization_loss = tf.add_n(tf.get_collection('losses', scope))
    loss = cross_entropy + regularization_loss
    return loss

# 计算每一个变量梯度的平均值。
# tower_grads 二维list: [
# 第1个gpu: [(grad1, var1),(grad2,var2),(grad3,var3),(grad4,var4)],
# 第2个gpu: [(grad1, var1),(grad2,var2),(grad3,var3),(grad4,var4)],
# 第3个gpu: [(grad1, var1),(grad2,var2),(grad3,var3),(grad4,var4)],
# 第4个gpu: [(grad1, var1),(grad2,var2),(grad3,var3),(grad4,var4)]
# ]
# Note that each grad_and_vars looks like the following:
def average_gradients(tower_grads): #
    average_grads = []
    print("tower_grads:",tower_grads)
    print("zip_tower_grads:",zip(*tower_grads))
    # 枚举所有的变量和变量在不同GPU上计算得出的梯度。
    for grad_and_vars in zip(*tower_grads):
        print("grad_and_vars:",grad_and_vars) # 长度为4的元组 (gpu1 (grad1,var1),gpu2 (grad1,var1), gpu3:(grad1,var1), gpu4:(grad1,var1))
        # 计算所有GPU上的梯度平均值。
        grads = []
        for g, _ in grad_and_vars:
            expanded_g = tf.expand_dims(g, 0)  # 插入第0维
            print("g:",g ," expanded_g:",expanded_g)
            grads.append(expanded_g)
        #
        grad = tf.concat(grads, 0)
        print("grads:",grads," grad:",grad) # gard:(4, 784, 500),即 N* widht*height
        grad = tf.reduce_mean(grad, 0) # 将N个梯度进行平均

        v = grad_and_vars[0][1] # 得到g对应的变量,4个gpu中取一个变量即可
        grad_and_var = (grad, v)
        # 将变量和它的平均梯度对应起来。
        average_grads.append(grad_and_var) # [(avg_grad1,var1),(avg_grad2,var2),...,(avg_grad4,var4)]
    # 返回所有变量的平均梯度，这个将被用于变量的更新。
    return average_grads

# 主训练过程。
def main(argv=None): 
    # 将简单的运算放在CPU上，只有神经网络的训练过程放在GPU上。
    with tf.Graph().as_default(), tf.device('/cpu:0'):

        # 定义基本的训练过程
        x, y_ = get_input()
        regularizer = tf.contrib.layers.l2_regularizer(REGULARAZTION_RATE)
        
        global_step = tf.get_variable('global_step', [], initializer=tf.constant_initializer(0), trainable=False)
        learning_rate = tf.train.exponential_decay(
            LEARNING_RATE_BASE, global_step, 60000 / BATCH_SIZE, LEARNING_RATE_DECAY)       
        
        opt = tf.train.GradientDescentOptimizer(learning_rate)
        
        tower_grads = []
        reuse_variables = False
        # 将神经网络的优化过程跑在不同的GPU上。
        for i in range(N_GPU):
            with tf.device('/gpu:%d' % i):
                with tf.name_scope('GPU_%d' % i) as scope: # name_scope并不会影响get_variable的命名空间
                    cur_loss = get_loss(x, y_, regularizer, scope, reuse_variables) # 总共有4个变量，2个weight以及2个bias
                    reuse_variables = True
                    grads = opt.compute_gradients(cur_loss) # A list of (gradient, variable) pairs. [(grad1, var1),(grad2,var2),(grad3,var3),(grad4,var4)]
                    # 之所以有4个梯度，是因为有4个变量，weight1, bias1, weight2,bias2,注意此处与gpu 的个数无关
                    tower_grads.append(grads)
        
        # 计算变量的平均梯度。
        # 变量是共享的，将所有gpu上的梯度进行求和平均
        grad_and_vars = average_gradients(tower_grads) # [ 第1个gpu: [(g1, v1),(g2,v2),(g3,v3),(g4,v4)], 第2个gpu: [(g1, v1),(g2,v2),(g3,v3),(g4,v4)],...]
        for grad, var in grad_and_vars:
            if grad is not None:
                tf.summary.histogram('gradients_on_average/%s' % var.op.name, grad)

        # 使用平均梯度更新参数。
        apply_gradient_op = opt.apply_gradients(grad_and_vars, global_step=global_step)
        for var in tf.trainable_variables():
            tf.summary.histogram(var.op.name, var)

        # 计算变量的滑动平均值。
        variable_averages = tf.train.ExponentialMovingAverage(MOVING_AVERAGE_DECAY, global_step)
        variables_to_average = (tf.trainable_variables() +tf.moving_average_variables())
        variables_averages_op = variable_averages.apply(variables_to_average)
        # 每一轮迭代需要更新变量的取值并更新变量的滑动平均值。
        train_op = tf.group(apply_gradient_op, variables_averages_op)
        #sys.exit(-1)
        saver = tf.train.Saver(tf.all_variables())
        summary_op = tf.summary.merge_all()
        init = tf.initialize_all_variables()
        with tf.Session(config=tf.ConfigProto(allow_soft_placement=True, log_device_placement=True)) as sess:
            # 初始化所有变量并启动队列。
            init.run()
            coord = tf.train.Coordinator()
            threads = tf.train.start_queue_runners(sess=sess, coord=coord)
            summary_writer = tf.summary.FileWriter(MODEL_SAVE_PATH, sess.graph)

            for step in range(TRAINING_STEPS):
                # 执行神经网络训练操作，并记录训练操作的运行时间。
                start_time = time.time()
                _, loss_value = sess.run([train_op, cur_loss])
                duration = time.time() - start_time
                
                # 每隔一段时间数据当前的训练进度，并统计训练速度。
                if step != 0 and step % 10 == 0:
                    # 计算使用过的训练数据个数。
                    num_examples_per_step = BATCH_SIZE * N_GPU
                    examples_per_sec = num_examples_per_step / duration
                    sec_per_batch = duration / N_GPU
    
                    # 输出训练信息。
                    format_str = ('%s: step %d, loss = %.2f (%.1f examples/sec; %.3f sec/batch)')
                    print (format_str % (datetime.now(), step, loss_value, examples_per_sec, sec_per_batch))
                    
                    # 通过TensorBoard可视化训练过程。
                    summary = sess.run(summary_op)
                    summary_writer.add_summary(summary, step)
    
                # 每隔一段时间保存当前的模型。
                if step % 1000 == 0 or (step + 1) == TRAINING_STEPS:
                    checkpoint_path = os.path.join(MODEL_SAVE_PATH, MODEL_NAME)
                    saver.save(sess, checkpoint_path, global_step=step)
        
            coord.request_stop()
            coord.join(threads)
        
if __name__ == '__main__':
    tf.app.run()



