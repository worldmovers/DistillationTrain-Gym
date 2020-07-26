from Utils.memory import Memory
from Nets.Critic import Critic
from Nets.P_actor import ParameterAgent
from Env.DC_gym_reward import DC_gym_reward as DC_Gym
from Env.STANDARD_CONFIG import CONFIG
standard_args = CONFIG(1).get_config()
import numpy as np
import math
import tensorflow as tf
physical_devices = tf.config.list_physical_devices('GPU')
tf.config.experimental.set_memory_growth(physical_devices[0], True)

class Agent:
    def __init__(self, summary_writer, total_episodes=500, env=DC_Gym(*standard_args), alpha=0.0001, batch_size=20, mem_length=100,
                 gamma=0.99):
        assert batch_size < mem_length
        self.total_episodes = total_episodes
        self.env = env
        self.batch_size = batch_size
        self.memory = Memory(max_size=mem_length)
        beta = alpha/10
        self.param_model, self.param_optimizer = \
            ParameterAgent(beta, env.continuous_action_space.shape[0], env.observation_space.shape).build_network()
        self.critic_model, self.critic_optimizer = \
            Critic(alpha, env.continuous_action_space.shape[0],
                      env.observation_space.shape).build_network()
        self.gamma = gamma
        self.history = []
        self.summary_writer = summary_writer
        self.step = 0

    def populate_memory(self):
        _ = self.env.reset()
        while len(self.memory.buffer) < self.memory.buffer.maxlen:
            state = self.env.State.state.copy()
            action = self.env.sample()
            action_continuous, action_discrete = action
            tops_state, bottoms_state, annual_revenue, TAC, done, info = self.env.step(action)
            if action_discrete == 0:  # seperating action
                self.memory.add((state, action_continuous, action_discrete, annual_revenue, TAC, tops_state, bottoms_state,
                                 1 - done))
            if done is True:
                _ = self.env.reset()

    def run_episodes(self):
        for i in range(self.total_episodes):
            state = self.env.reset()
            done = False
            total_reward = 0
            while not done:
                self.step += 1
                state = self.env.State.state.copy()
                action_continuous = np.clip(self.param_model.predict(state[np.newaxis, :]) + 0.3 *
                                            np.random.normal(0, 1, size=self.env.continuous_action_space.shape[0]),
                                            a_min=-1, a_max=1)
                Q_value = np.sum(self.critic_model.predict([state[np.newaxis, :], action_continuous]), axis=0)
                action_discrete = self.eps_greedy(Q_value, i, self.total_episodes)
                action_continuous = action_continuous[0]

                action = action_continuous, action_discrete
                # now take action
                tops_state, bottoms_state, annual_revenue, TAC, done, info = self.env.step(action)
                reward = annual_revenue + TAC
                total_reward += reward
                if action_discrete == 0:  # seperating action
                    self.memory.add(
                        (state, action_continuous, action_discrete, annual_revenue, TAC, tops_state, bottoms_state,
                         1 - done))
                self.learn()

            self.history.append(total_reward)


            if i % (self.total_episodes/10) == 0:
                print(f"Average reward of last {round(self.total_episodes/10)} episodes: "
                      f"{np.mean(self.history[-round(self.total_episodes/10):])}")
                print(f"episode {i}/{self.total_episodes}")

    def learn(self):
        # now sample from batch & train
        batch = self.memory.sample(self.batch_size)
        state_batch = np.array([each[0] for each in batch])
        continuous_action_batch = np.array([each[1] for each in batch])
        discrete_action_batch = np.array([each[2] for each in batch])
        annual_revenue_batch = np.array([each[3] for each in batch])
        TAC_batch = np.array([each[4] for each in batch])
        tops_state_batch = np.array([each[5] for each in batch])
        bottoms_state_batch = np.array([each[6] for each in batch])
        done_batch = np.array([each[7] for each in batch])

        # next state includes tops and bottoms
        next_continuous_action_tops = self.param_model.predict_on_batch(tops_state_batch)
        next_continuous_action_bottoms = self.param_model.predict_on_batch(bottoms_state_batch)
        Q_value_top = tf.keras.backend.sum(self.critic_model.predict_on_batch(
            [tops_state_batch, next_continuous_action_tops]), axis=0)
        Q_value_bottoms = tf.keras.backend.sum(self.critic_model.predict_on_batch(
            [bottoms_state_batch, next_continuous_action_bottoms]), axis=0)
        # target
        # note we don't use the actual done values because the max function is doing a version of this
        target_next_state_value = self.gamma * (tf.math.maximum(Q_value_top, 0) + tf.math.maximum(Q_value_bottoms, 0))
        with tf.GradientTape(persistent=True) as tape:
            predict_param = self.param_model.predict_on_batch(state_batch)
            Q_value = tf.keras.backend.sum(self.critic_model.predict_on_batch([state_batch, predict_param]), axis=0)
            loss_param = - Q_value

            # compute Q net updates
            # first for TAC and revenue which is simple
            revenue_prediction, TAC_prediction, future_reward_prediction = \
                self.critic_model.predict_on_batch([state_batch, continuous_action_batch])
            loss_revenue = tf.keras.losses.MSE(revenue_prediction,
                                               tf.convert_to_tensor(annual_revenue_batch, dtype=np.float32))
            loss_TAC = tf.keras.losses.MSE(TAC_prediction, tf.convert_to_tensor(TAC_batch, dtype=np.float32))
            loss_next_state_value = tf.keras.losses.MSE(tf.convert_to_tensor(target_next_state_value, np.float32),
                                                        future_reward_prediction)

        with self.summary_writer.as_default():
            tf.summary.scalar("param batch mean loss", tf.math.reduce_mean(loss_param), step=self.step)
            tf.summary.scalar("revenue batch mean loss", tf.math.reduce_mean(loss_revenue), step=self.step)
            tf.summary.scalar("TAC batch mean loss", tf.math.reduce_mean(loss_TAC), step=self.step)
            tf.summary.scalar("next_state_value batch mean loss", tf.math.reduce_mean(loss_next_state_value), step=self.step)
            tf.summary.scalar("total DQN loss", tf.math.reduce_mean(loss_TAC) + tf.math.reduce_mean(loss_revenue) +
                              tf.math.reduce_mean(loss_next_state_value), step=self.step)

        # get gradients of loss with respect to the param_model weights
        gradient_param = tape.gradient(loss_param, self.param_model.trainable_weights)
        gradient_next_state_value = tape.gradient(loss_next_state_value, self.critic_model.trainable_weights,
                                     unconnected_gradients=tf.UnconnectedGradients.ZERO)  # not including revenue and loss
        gradient_revenue = tape.gradient(loss_revenue, self.critic_model.trainable_weights,
                                         unconnected_gradients=tf.UnconnectedGradients.ZERO)
        gradient_TAC = tape.gradient(loss_TAC, self.critic_model.trainable_weights,
                                     unconnected_gradients=tf.UnconnectedGradients.ZERO)
        gradient_dqn_total = [dqn_grad + gradient_TAC[i] + gradient_revenue[i]
                              for i, dqn_grad in enumerate(gradient_next_state_value)]

        # update global parameters
        self.param_optimizer.apply_gradients(zip(gradient_param, self.param_model.trainable_weights))
        self.critic_optimizer.apply_gradients(zip(gradient_dqn_total, self.critic_model.trainable_weights))

    def eps_greedy(self, Q_value, current_step, stop_step, max_prob=1, min_prob=0):
        explore_threshold = max(max_prob - current_step / stop_step * (max_prob - min_prob), min_prob)
        random = np.random.rand()
        if random < explore_threshold:  # explore probabilities to bias in direction of seperating a stuff
            action_discrete = np.random.choice(a=[0, 1], p=[0.7, 0.3])
        else:  # exploit
            if Q_value > 0:  #  seperate
                action_discrete = 0
            else:  # submit
                action_discrete = 1
        return action_discrete
