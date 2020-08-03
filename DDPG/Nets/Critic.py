from tensorflow.keras.layers import Dense, Input, Concatenate, Flatten
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import RMSprop, Adam
import tensorflow as tf


class Critic:
    def __init__(self, lr, n_continuous_actions, state_shape, layer_size=128):
        self.lr = lr
        self.n_continuous_actions = n_continuous_actions
        self.layer_size = layer_size
        self.state_shape = state_shape


    def build_network(self):
        input_state = Input(shape=self.state_shape, name="input_state")
        input_parameters = Input(shape=(self.n_continuous_actions,), name="input_parameters")
        flat_input_state = Flatten(name="flat_input_state")(input_state)
        flat_input_parameters = Flatten(name="flat_input_parameters")(input_parameters)
        inputs = Concatenate(name="concat")([flat_input_state, flat_input_parameters])

        dense1 = Dense(self.layer_size, activation='relu', name="dense1")(inputs)
        dense2 = Dense(self.layer_size, activation= 'relu', name="dense2")(dense1)
        dense3 = Dense(self.layer_size, activation='relu', name="dense3")(dense2)

        future_reward = Dense(1, activation='linear', name="future_reward")(dense3)
        revenue = Dense(1, activation='linear', name="revenue")(dense3)
        cost = Dense(1, activation='linear', name="TAC")(dense3)

        model = Model(inputs=[input_state, input_parameters], outputs=[future_reward, revenue, cost])
        #optimizer = RMSprop(lr=self.lr)
        optimizer = Adam(lr=self.lr)

        return model, optimizer