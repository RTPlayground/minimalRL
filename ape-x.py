# credit to: https://github.com/jsrimr
import random

import collections
import gymnasium as gym
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

# Hyperparameters
learning_rate = 0.0005
gamma = 0.98
buffer_limit = 50000
batch_size = 32


class ReplayBuffer():
    def __init__(self):
        self.buffer = collections.deque(maxlen=buffer_limit)

    def sample(self, n):
        return random.sample(self.buffer, n)

    def size(self):
        return len(self.buffer)


class Qnet(nn.Module):
    def __init__(self):
        super(Qnet, self).__init__()
        self.fc1 = nn.Linear(4, 256)
        self.fc2 = nn.Linear(256, 2)

    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return x

    def sample_action(self, obs, epsilon):
        out = self.forward(obs)
        coin = random.random()
        if coin < epsilon:
            return random.randint(0, 1)
        else:
            return out.argmax().item()


def learner_process(device, model, target_model, exp_q):
    leaner = Learner(device, model, target_model, exp_q)
    leaner.run()


class Learner:
    def __init__(self, device, model, target_model, conn):
        self.memory = ReplayBuffer()
        self.device = device
        self.q = model
        self.q_target = target_model
        self.optimizer = optim.Adam(self.q.parameters())
        self.n_epochs = 0
        self.conn = conn

    def run(self):
        while True:
            if self.memory.size() > 2000:
                self.train()
                self.n_epochs += 1
                if self.n_epochs % 10 == 0:
                    self.q_target.load_state_dict(self.q.state_dict())

            while not self.conn.empty():
                try:
                    experience = self.conn.get()
                    self.memory.buffer.append(experience)
                except:
                    print("memory load failed")

    def train(self):
        for i in range(3):
            mini_batch = self.memory.sample(batch_size)
            s_lst, a_lst, r_lst, s_prime_lst, done_mask_lst = [], [], [], [], []

            for transition in mini_batch:
                s, a, r, s_prime, done_mask = transition
                s_lst.append(s)
                a_lst.append([a])
                r_lst.append([r])
                s_prime_lst.append(s_prime)
                done_mask_lst.append([done_mask])

            s, a, r, s_prime, done_mask = torch.tensor(s_lst, dtype=torch.float).to(self.device), torch.tensor(a_lst).to(self.device), \
                                          torch.tensor(r_lst).to(self.device), torch.tensor(s_prime_lst, dtype=torch.float).to(self.device), \
                                          torch.tensor(done_mask_lst).to(self.device)

            q_out = self.q(s)
            q_a = q_out.gather(1, a)
            max_q_prime = self.q_target(s_prime).max(1)[0].unsqueeze(1)
            target = r + gamma * max_q_prime * done_mask
            loss = F.smooth_l1_loss(q_a, target)

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()


def actor_process(actor_id, n_actors, model, target_model, exp_q, device):
    actor = Actor(actor_id, n_actors, model, target_model, exp_q, device)
    actor.run()


class Actor:
    def __init__(self, actor_id, n_actors, model, target_model, conn, device):
        self.env = gym.make('CartPole-v1', render_mode = 'rgb_array')
        self.device = device
        self.state, info = self.env.reset()

        self.actor_id = actor_id
        self.epsilon = 0.1 + (actor_id / 7) / n_actors  # 0.4 ** (1 + actor_id * 7 / (n_actors - 1))

        self.memory = ReplayBuffer()
        self.q = model
        self.q_target = target_model
        self.episode_reward = 0
        self.n_episodes = 0
        self.net_load_interval = 10

        self.conn = conn

    def run(self):
        while True:
            observation = self.state
            epsilon = max(0.01, self.epsilon - 0.01 * (self.n_episodes / 200))  # Linear annealing from 8% to 1%
            action = self.q.sample_action(torch.from_numpy(observation).float().to(self.device), epsilon)

            observation_prime, reward, terminated, truncated, info = self.env.step(action)
            done_mask = 0.0 if terminated else 1.0
            self.conn.put((observation, action, reward / 100.0, observation_prime, done_mask))
            self.state = observation_prime

            self.episode_reward += reward

            if terminated or truncated:  # episode ends
                self.state, info = self.env.reset()
                self.n_episodes += 1

                if self.n_episodes % 20 == 0:
                    print('episodes:', self.n_episodes, 'actor_id:', self.actor_id, 'reward:', self.episode_reward)
                self.episode_reward = 0


import torch.multiprocessing as mp


def main():
    torch.multiprocessing.set_start_method('spawn')# good solution !!!!
    if torch.cuda.is_available():
        device= 'cuda:0'
    else:
        device = 'cpu'
    print('device:{}'.format(device))
    model = Qnet().to(device)
    target_model = Qnet().to(device)
    target_model.load_state_dict(model.state_dict())
    model.share_memory()
    target_model.share_memory()

    q = mp.Queue()

    # learner process
    processes = [mp.Process(
        target=learner_process,
        args=(device, model, target_model, q))]

    # actor process
    n_actors = 2
    for actor_id in range(n_actors):
        processes.append(mp.Process(
            target=actor_process,
            args=(actor_id, n_actors, model, target_model, q, device)))

    for i in range(len(processes)):
        processes[i].start()

    for p in processes:
        p.join()


if __name__ == '__main__':
    main()