import torch
from torch import nn as nn
import numpy as np

from generative_playground.utils.gpu_utils import to_gpu, FloatTensor, LongTensor
from generative_playground.models.decoder.policy import SimplePolicy



class OneStepDecoderContinuous(nn.Module):
    def __init__(self,model):
        '''
        Wrapper for a continuous decoder that doesn't look at last action chosen, eg simple RNN
        :param model:
        '''
        super().__init__()
        self.model = to_gpu(model)
        self.model.eval()

    def init_encoder_output(self, z):
        self.n = 0
        self.z = z
        self.z_size = z.size()[-1]
        try:
            self.model.init_encoder_output(z)
        except:
            pass
        self.logits = self.model.forward(z)

    def forward(self, action=None):
        '''
        Gets the output sequence all at once, then feeds it back one step at a time
        :param action: ignored
        :return: a vector of logits over next actions
        '''
        if self.n < self.logits.shape[1]:
            out = torch.squeeze(self.logits[:, self.n, :],1)
            self.n +=1
            return out
        else:
            raise StopIteration()

# class SimpleDiscreteDecoder(nn.Module):
#     def __init__(self, stepper, policy: SimplePolicy, bypass_actions=False): #mask_gen = None,
#         '''
#         A simple discrete decoder, alternating getting logits from model and actions from policy
#         :param stepper:
#         :param policy: choose an action from the logits, can be max, or random sample,
#         or choose from pre-determined target sequence. Only depends on current logits + history,
#         can't handle multi-step strategies like beam search
#         :param mask_fun: takes in one-hot encoding of previous action (for now that's all we care about)
#         '''
#         super().__init__()
#         self.stepper = to_gpu(stepper)
#         self.policy = policy
#         self.bypass_actions = bypass_actions
#
#     def forward(self, z):
#         # initialize the decoding model
#         self.stepper.init_encoder_output(z)
#         if self.bypass_actions:
#             return None, self.stepper.logits
#         out_logits = []
#         out_actions = []
#         last_action = [None]*len(z)
#         step = 0
#         # as it's PyTorch, can determine max_len dynamically, by when the stepper raises StopIteration
#         while True:
#             try:
#   #          if True:
#                 #  batch x num_actions
#                 next_logits = self.stepper(last_action=last_action)
#                 next_action = self.policy(next_logits)
#                 out_logits.append(next_logits)
#                 out_actions.append(next_action)
#                 last_action = next_action
#             except StopIteration as e:
#                 # StopIteration is how the model signals it's done
#                 break
#         out_actions_all = torch.cat(out_actions, 1)
#         out_logits_all = torch.cat(out_logits, 1)
#         return out_actions_all, out_logits_all

class SimpleDiscreteDecoderWithEnv(nn.Module):
    def __init__(self,
                 stepper,
                 policy: SimplePolicy,
                 task=None,
                 mask_gen=None,
                 batch_size=None):
        '''
        A simple discrete decoder, alternating getting logits from model and actions from policy
        :param stepper:
        :param policy: choose an action from the logits, can be max, or random sample,
        or choose from pre-determined target sequence. Only depends on current logits + history,
        can't handle multi-step strategies like beam search
        :param mask_fun: takes in one-hot encoding of previous action (for now that's all we care about)
        :param task: environment that returns rewards and whether the episode is finished
        '''
        super().__init__()
        self.stepper = to_gpu(stepper)
        self.policy = policy
        self.task = task
        self.bypass_actions = False # legacy
        self.mask_gen = mask_gen
        self.output_shape = [None, None, self.stepper.output_shape[-1]]
        self.batch_size = batch_size

    def forward(self, z=None):
        # initialize the decoding model
        self.stepper.init_encoder_output(z)
        if self.mask_gen is not None:
            self.mask_gen.reset()
        if self.bypass_actions:
            return None, self.stepper.logits
        out_logits = []
        out_actions = []
        out_rewards = []
        out_terminals = []
        if self.task is not None:
            last_state = self.task.reset()
        elif z is not None:
            last_state = [None]*len(z)
        else:
            last_state = [None]*self.batch_size

        step = 0
        # as it's PyTorch, can determine max_len dynamically, by when the stepper raises StopIteration
        while True:
            try:
                if 'ndarray' in str(type(last_state)):
                    last_state = to_pytorch(last_state)
                #  batch x num_actions
                next_logits = self.stepper(last_state)
                # #just in case we were returned a sequence of length 1 rather than a straight batch_size x num_actions
                # next_logits = torch.squeeze(next_logits, 1)
                # if self.mask_gen is not None:
                #     mask = FloatTensor(self.mask_gen(last_state))
                #     next_logits = next_logits - 1e4 * (1 - mask)

                next_action = self.policy(next_logits)
                out_logits.append(next_logits)
                out_actions.append(next_action)
                if self.task is None:
                    last_state = next_action
                else:
                    # TODO does that play nicely after sequence end?
                    last_state, rewards, terminals, _ = self.task.step(next_action.detach().cpu().numpy())
                    out_rewards.append(to_pytorch(rewards))
                    out_terminals.append(to_pytorch(terminals))
            except StopIteration as e:
                #print(e)
                break
            out_actions_all = torch.cat([x.unsqueeze(1) for x in out_actions] , 1)
            #out_logits_all = torch.cat(out_logits, 1)
            out_logits_all = torch.cat([x.unsqueeze(1) for x in out_logits], 1)


        if self.task is None:
                    return out_actions_all, out_logits_all
        else:
            out_rewards_all = torch.cat([x.unsqueeze(1) for x in out_rewards], 1)
            out_terminals_all = torch.cat([x.unsqueeze(1) for x in out_terminals], 1)
            return out_actions_all, out_logits_all, out_rewards_all, out_terminals_all

def to_pytorch(x):
    if 'ndarray' in str(type(x)):
        if 'bool' in str(type(x[0])):
            x = np.array([1.0 if xi else 0.0 for xi in x]).astype(np.float32)
            return to_gpu(torch.from_numpy(x)).to(torch.float32)
        else:
            if 'float' in str(type(x[0])):
                return to_gpu(torch.from_numpy(x)).to(torch.float32)
            else:
                return to_gpu(torch.from_numpy(x))
    else:
        return x