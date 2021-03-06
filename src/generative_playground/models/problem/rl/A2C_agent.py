#######################################################################
# Copyright (C) 2017 Shangtong Zhang(zhangshangtong.cpp@gmail.com)    #
# Permission given to modify the code as long as you keep this        #
# declaration at the top                                              #
#######################################################################

from deep_rl.network import *
from deep_rl.agent.BaseAgent import *

from generative_playground.utils.gpu_utils import to_gpu, FloatTensor

class A2CAgent(BaseAgent):
    def __init__(self, config):
        BaseAgent.__init__(self, config)
        self.config = config
        self.task = config.task_fn()
        self.network = config.network_fn(self.task.state_dim, self.task.action_dim)
        self.optimizer = config.optimizer_fn(filter(lambda p: p.requires_grad, self.network.parameters()))
        self.total_steps = 0
        self.reset_state()

    def reset_state(self):
        self.states = self.task.reset()
        self.episode_rewards = np.zeros(self.config.num_workers)
        try:
            self.network.mask_gen.reset()
        except:
            pass
        self.remember_step(True)
        #self.last_episode_rewards = np.zeros(self.config.num_workers)

    def remember_step(self, remember_step):
        # TODO: weave it more naturally into the codec
        self.network.remember_step = remember_step
        self.network.network.phi_body.model.remember_step = remember_step

    def iteration(self):
        config = self.config
        rollout = []
        states = self.states
        last_iter = False
        for _ in range(config.rollout_length):
            try:
                actions, log_probs, entropy, values = self.network.predict(config.state_normalizer(states))
            except StopIteration: # if the stateful network has reached max length
                last_iter = True
                break
            next_states, rewards, terminals, _ = self.task.step(actions.detach().cpu().numpy())
            self.episode_rewards += rewards
            rewards = config.reward_normalizer(rewards)
            # for i, terminal in enumerate(terminals):
            #     if terminals[i]:
            #         self.last_episode_rewards[i] = self.episode_rewards[i]
            #         self.episode_rewards[i] = 0

            rollout.append([log_probs, values, actions, rewards, 1 - terminals, entropy])
            states = next_states


        self.states = states
        #self.remember_step(False)
        pending_value = self.network.predict(config.state_normalizer(states), remember_step=False)[-1]
        #self.remember_step(True)
        rollout.append([None, pending_value, None, None, None, None])

        processed_rollout = [None] * (len(rollout) - 1)
        advantages = to_gpu(torch.from_numpy(np.zeros((config.num_workers, 1))))
        returns = pending_value.detach()
        for i in reversed(range(len(rollout) - 1)):
            log_prob, value, actions, rewards, terminals, entropy = rollout[i]
            terminals = to_gpu(torch.from_numpy(terminals*1.0)).unsqueeze(1).type(FloatTensor)
            rewards = to_gpu(torch.from_numpy(rewards*1.0)).unsqueeze(1).type(FloatTensor)
            next_value = rollout[i + 1][1]
            returns = rewards + config.discount * terminals * returns
            if not config.use_gae:
                advantages = returns - value.detach()
            else:
                td_error = rewards + config.discount * terminals * next_value.detach() - value.detach()
                advantages = advantages * config.gae_tau * config.discount * terminals + td_error
            processed_rollout[i] = [log_prob, value, returns, advantages, entropy]

        log_prob, value, returns, advantages, entropy = map(lambda x: torch.cat(x, dim=0), zip(*processed_rollout))
        policy_loss = -log_prob * advantages
        value_loss = 0.5 * (returns - value).pow(2)
        entropy_loss = entropy.mean()

        self.policy_loss = np.mean(policy_loss.cpu().detach().numpy())
        self.entropy_loss = np.mean(entropy_loss.cpu().detach().numpy())
        self.value_loss = np.mean(value_loss.cpu().detach().numpy())

        self.optimizer.zero_grad()
        (policy_loss - config.entropy_weight * entropy_loss +
         config.value_loss_weight * value_loss).mean().backward(retain_graph=not last_iter)
        nn.utils.clip_grad_norm_(self.network.parameters(), config.gradient_clip)
        self.optimizer.step()

        #steps = config.rollout_length * config.num_workers
        self.total_steps += 1
        if last_iter:
            raise StopIteration
