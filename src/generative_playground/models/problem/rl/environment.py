import numpy as np
from generative_playground.codec.codec import get_codec
from generative_playground.molecules.model_settings import get_settings
from rdkit import Chem

class SequenceEnvironment:
    def __init__(self,
                 molecules=True,
                 grammar=True,
                 reward_fun=None,
                 batch_size=1,
                 max_steps=None,
                 save_dataset=None):
        settings = get_settings(molecules, grammar)
        self.codec = get_codec(molecules, grammar, settings['max_seq_length'])
        self.action_dim = self.codec.feature_len()
        self.state_dim = self.action_dim
        if max_steps is None:
            self._max_episode_steps = settings['max_seq_length']
        else:
            self._max_episode_steps = max_steps
        self.reward_fun = reward_fun
        self.batch_size = batch_size
        self.save_dataset = save_dataset
        self.smiles = None
        self.seq_len = None
        self.valid = None
        self.actions = None
        self.done_rewards = None
        self.reset()

    def reset(self):
        self.actions = []
        self.done_rewards = [None for _ in range(self.batch_size)]
        self.smiles = [None for _ in range(self.batch_size)]
        self.seq_len = np.zeros([self.batch_size])
        self.valid = np.zeros([self.batch_size])
        return [None]*self.batch_size

    def step(self, action):
        '''
        Convention says environment outputs np.arrays
        :param action: LongTensor(batch_size), or np.array(batch_sizelast discrete action chosen
        :return:
        '''
        try: # in case action is a torch.Tensor
            action = action.cpu().to_numpy()
        except:
            pass

        self.actions.append(action[:,None])

        next_state = action
        if len(self.actions) < self._max_episode_steps:
            done = self.codec.is_padding(action) # max index is padding, by convention
        else:
            done = np.ones_like(action) == 1

        reward = np.zeros_like(action, dtype=np.float)
        # for those sequences just computed, calculate the reward
        for i in range(len(action)):
            if self.done_rewards[i] is None and done[i]:
                this_action_seq = np.concatenate(self.actions, axis=1)[i:(i+1),:]
                this_char_seq = self.codec.actions_to_strings(this_action_seq) # codec expects a batch
                self.smiles[i] = this_char_seq[0]
                this_mol = Chem.MolFromSmiles(self.smiles[i])
                if this_mol is None:
                    print(self.smiles[i])
                    # rules = self.codec.grammar.GCFG.productions()
                    # for a in this_action_seq[0]:
                    #     print(rules[a])
                    self.valid[i] = 0
                else:
                    self.valid[i] = 1
                this_reward = self.reward_fun(this_char_seq)[0]
                self.done_rewards[i] = this_reward
                reward[i] = this_reward
                self.seq_len[i] = len(self.actions)

        #TODO: put the special string handling into the hdf5 wrapper
        import h5py
        dt = h5py.special_dtype(vlen=str)  # PY3 hdf5 datatype for variable-length Unicode strings
        if len(self.actions) == self._max_episode_steps:
            # dump the whole batch to disk
            append_data = {'smiles': np.array(self.smiles, dtype=dt),
                           'actions': np.concatenate(self.actions, axis=1),
                           'seq_len': self.seq_len}
            if self.save_dataset is not None:
                self.save_dataset.append(append_data)

        if False and not all(reward==reward):
            print('failure!')

        return next_state, reward, done, (self.smiles, self.valid)

    def seed(self, random_seed):
        return random_seed