import tensorflow as tf
import se3
import tools
import config as cfg


# CNN Block
def cnn_model(inputs):
    with tf.variable_scope("cnn_model"):
        conv_1 = tf.contrib.layers.conv2d(inputs, num_outputs=64, kernel_size=(7, 7,),
                                          stride=(2, 2), padding="same", scope="conv_1", data_format="NCHW")
        conv_2 = tf.contrib.layers.conv2d(conv_1, num_outputs=128, kernel_size=(5, 5,),
                                          stride=(2, 2), padding="same", scope="conv_2", data_format="NCHW")

        conv_3 = tf.contrib.layers.conv2d(conv_2, num_outputs=256, kernel_size=(5, 5,),
                                          stride=(2, 2), padding="same", scope="conv_3", data_format="NCHW")
        conv_3_1 = tf.contrib.layers.conv2d(conv_3, num_outputs=256, kernel_size=(3, 3,),
                                            stride=(1, 1), padding="same", scope="conv_3_1", data_format="NCHW")

        conv_4 = tf.contrib.layers.conv2d(conv_3_1, num_outputs=512, kernel_size=(3, 3,),
                                          stride=(2, 2), padding="same", scope="conv_4", data_format="NCHW")
        conv_4_1 = tf.contrib.layers.conv2d(conv_4, num_outputs=512, kernel_size=(3, 3,),
                                            stride=(1, 1), padding="same", scope="conv_4_1", data_format="NCHW")

        conv_5 = tf.contrib.layers.conv2d(conv_4_1, num_outputs=512, kernel_size=(3, 3,),
                                          stride=(2, 2), padding="same", scope="conv_5", data_format="NCHW")
        conv_5_1 = tf.contrib.layers.conv2d(conv_5, num_outputs=512, kernel_size=(3, 3,),
                                            stride=(1, 1), padding="same", scope="conv_5_1", data_format="NCHW")

        conv_6 = tf.contrib.layers.conv2d(conv_5_1, num_outputs=1024, kernel_size=(3, 3,),
                                          stride=(2, 2), padding="same", scope="conv_6", data_format="NCHW")
        return conv_6


def fc_model(inputs):
    with tf.variable_scope("fc_model"):
        fc_128 = tf.contrib.layers.fully_connected(inputs, 128, scope="fc_128", activation_fn=tf.nn.relu)
        fc_12 = tf.contrib.layers.fully_connected(fc_128, 12, scope="fc_12", activation_fn=tf.nn.relu)
        return fc_12


def se3_comp_over_timesteps(fc_timesteps):
    with tf.variable_scope("se3_comp_over_timesteps"):
        # position + orientation in quat
        initial_pose = tf.constant([0, 0, 0, 1, 0, 0, 0], tf.float32)

        poses = []
        pose = initial_pose
        fc_ypr_poses = tf.unstack(fc_timesteps[:, 0:6], axis=0)  # take the x, y, z, y, p, r
        for d_ypr_pose in fc_ypr_poses:
            pose = se3.se3_comp(pose, d_ypr_pose)
            poses.append(pose)
        return tf.stack(poses)


def cudnn_lstm_unrolled(inputs, initial_state):
    lstm = tf.contrib.cudnn_rnn.CudnnLSTM(cfg.lstm_layers, cfg.lstm_size)
    outputs, final_state = lstm(inputs, initial_state=initial_state)
    return outputs, final_state


def cnn_over_timesteps(inputs):
    with tf.variable_scope("cnn_over_timesteps"):
        unstacked_inputs = tf.unstack(inputs, axis=0)

        outputs = []

        for i in range(len(unstacked_inputs) - 1):
            # stack images along channels
            image_stacked = tf.concat((unstacked_inputs[i], unstacked_inputs[i + 1]), axis=1)
            outputs.append(cnn_model(image_stacked))

        return tf.stack(outputs, axis=0)


def build_model(inputs, lstm_init_state):
    with tf.device("/gpu:0"):
        with tf.variable_scope("cnn_unrolled", reuse=tf.AUTO_REUSE):
            cnn_outputs = cnn_over_timesteps(inputs)

        cnn_outputs = tf.reshape(cnn_outputs, [cnn_outputs.shape[0], cnn_outputs.shape[1],
                                               cnn_outputs.shape[2] * cnn_outputs.shape[3] * cnn_outputs.shape[4]])

    with tf.device("/gpu:0"):
        # RNN Block
        with tf.variable_scope("rnn_unrolled", reuse=tf.AUTO_REUSE):
            lstm_init_state = tuple(tf.unstack(lstm_init_state))
            lstm_outputs, lstm_states = cudnn_lstm_unrolled(cnn_outputs, lstm_init_state)

    with tf.device("/gpu:0"):
        with tf.variable_scope("fc_unrolled", reuse=tf.AUTO_REUSE):
            fc_outputs = tools.static_map_fn(fc_model, lstm_outputs, axis=0)

    with tf.device("/gpu:0"):
        with tf.variable_scope("se3_unrolled", reuse=tf.AUTO_REUSE):
            # at this point the outputs from the fully connected layer are  [x, y, z, yaw, pitch, roll, 6 x covars]
            se3_outputs = tools.static_map_fn(se3_comp_over_timesteps, fc_outputs, axis=1)

    return fc_outputs, se3_outputs, lstm_states
