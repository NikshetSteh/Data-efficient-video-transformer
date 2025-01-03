import math
import os
import urllib
import urllib.request

import timm
import torch
from torch import nn


class TimeWarp(nn.Module):
    def __init__(self, baseModel, method='sqeeze', flatn=True):
        super(TimeWarp, self).__init__()
        self.baseModel = baseModel
        self.method = method
        self.flatn = flatn

    def forward(self, x):
        batch_size, time_steps, C, H, W = x.size()
        if self.method == 'loop':
            output = []
            for i in range(time_steps):
                # input one frame at a time into the basemodel
                x_t = self.baseModel(x[:, i, :, :, :])
                # Flatten the output
                if self.flatn:
                    x_t = x_t.view(x_t.size(0), -1)
                output.append(x_t)
            # end loop
            # make output as  ( samples, timesteps, output_size)
            x = torch.stack(output, dim=0).transpose_(0, 1)
        else:
            # reshape input to be (batch_size * timesteps, input_size)
            x = x.contiguous().view(batch_size * time_steps, C, H, W)
            x = self.baseModel(x)
            if self.flatn:
                x = x.view(x.size(0), -1)
            # make output as  ( samples, timesteps, output_size)
            x = x.contiguous().view(batch_size, time_steps, x.size(-1))
        # print(x.shape)
        return x


class extractlastcell(nn.Module):
    def forward(self, x):
        out, _ = x
        return out[:, -1, :]


# postiona encoder  give use the information of the postion or (time of frame in the seq)
# it will help us to learn the temproal feature
class PostionalEcnoder(nn.Module):
    def __init__(self, embd_dim, dropout=0.1, time_steps=30):
        # embd_dim == d_model
        # time_steps == max_len

        super(PostionalEcnoder, self).__init__()
        self.dropout = nn.Dropout(p=dropout)
        self.embd_dim = embd_dim
        self.time_steps = time_steps

    def do_pos_encode(self):

        device = 'cuda' if torch.cuda.is_available() else 'cpu'

        pe = torch.zeros(self.time_steps, self.embd_dim).to(device)
        for pos in range(self.time_steps):
            for i in range(0, self.embd_dim, 2):  # tow steps loop , for each dim in embddim
                pe[pos, i] = math.sin(pos / (10000 ** ((2 * i) / self.embd_dim)))
                pe[pos, i + 1] = math.cos(pos / (10000 ** ((2 * (i + 1)) / self.embd_dim)))
        pe = pe.unsqueeze(0)  # to make shape of (batch size , time steps ,embding_dim)
        return pe

    def forward(self, x):
        # x here is embded data must be shape of (batch , time_steps , embding_dim)
        x = x * math.sqrt(self.embd_dim)
        pe = self.do_pos_encode()
        x += pe[:, :x.size(1)]  # pe will automatically be expanded with the same batch size as encoded_words
        x = self.dropout(x)
        return x


class memoTransormer(nn.Module):
    def __init__(self, dim, heads=8, layers=6, actv='gelu'):
        super(memoTransormer, self).__init__()
        self.encoder_layer = nn.TransformerEncoderLayer(d_model=dim, nhead=heads, activation=actv)
        self.transformer_encoder = nn.TransformerEncoder(self.encoder_layer, num_layers=layers)

    def forward(self, x):
        x = self.transformer_encoder(x)
        return x


# orginal with and without wights can change num_class
# any base model + your transformation
def DeVTr(w='none', base='default', classifier='default', mid_layer=1024, mid_drop=0.4, num_classes=1, dim_embd=512,
          dr_rate=0.1, time_stp=40, encoder_stack=4, encoder_head=8):
    # defualt devter with wights , without wight
    # defualt change class numbers
    # change base cnn
    # change classifier

    if w == "default":
        w = os.path.join(
            os.path.expanduser("~"),
            ".menovideo/model.pth"
        )
        if not os.path.exists(w):
            if not os.path.exists(os.path.dirname(w)):
                os.mkdir(os.path.dirname(w))

            print("Downloading weights ...")
            # url = ("https://drive.usercontent.google.com/download?"
            #        "id=1s7Z1c-4zC522BFVM5EiZDMQLe6ZV8QQh&export=download&authuser=0&confirm=t")
            url = ("https://drive.usercontent.google.com/download?"
                   "id=1R8BC96P4zBKyweLjciUOsqg1EaBUJS3c&export=download&authuser=0&confirm=t")
            urllib.request.urlretrieve(
                url,
                w
            )
            print("Download done")
        else:
            print(f"Found model: {w}")

    if base == 'default':
        if w != 'none':
            num_classes = 1
            dr_rate = 0.1
            dim_embd = 512
            encoder_stack = 4
            encoder_head = 8
            time_stp = 40

        baseModel = timm.create_model('vgg19_bn', pretrained=True, num_classes=dim_embd)
        i = 0
        for child in baseModel.features.children():
            if i < 40:
                for param in child.parameters():
                    param.requires_grad = False
            else:
                for param in child.parameters():
                    param.requires_grad = True
            i += 1

        bas2 = nn.Sequential(baseModel,
                             nn.ReLU(), )

        model = nn.Sequential(TimeWarp(bas2, method='loop', flatn=False),
                              # PrintLayer(),
                              PostionalEcnoder(dim_embd, dropout=dr_rate, time_steps=time_stp),
                              memoTransormer(dim_embd, heads=encoder_head, layers=encoder_stack, actv='gelu'),
                              # PrintLayer(),
                              nn.Flatten(),
                              # PrintLayer(),
                              # 20480 is frame numbers * dim
                              nn.Linear(time_stp * dim_embd, 1024),
                              nn.Dropout(0.4),
                              nn.ReLU(),
                              nn.Linear(1024, num_classes)

                              )
        if w != 'none':
            if torch.cuda.is_available():
                model.load_state_dict(torch.load(w))
            else:
                model.load_state_dict(torch.load(w, map_location='cpu'))
    else:

        bas2 = nn.Sequential(base,
                             nn.ReLU(), )
        if classifier != 'default':
            model = nn.Sequential(TimeWarp(bas2, method='loop', flatn=False),
                                  # PrintLayer(),
                                  PostionalEcnoder(dim_embd, dropout=dr_rate, time_steps=time_stp),
                                  memoTransormer(dim_embd, heads=encoder_head, layers=encoder_stack, actv='gelu'),
                                  # PrintLayer(),
                                  nn.Flatten(),
                                  # PrintLayer(),
                                  # 20480 is frame numbers * dim
                                  nn.Linear(time_stp * dim_embd, mid_layer),
                                  nn.Dropout(mid_drop),
                                  nn.ReLU(),
                                  classifier

                                  )
        else:
            model = nn.Sequential(TimeWarp(bas2, method='loop', flatn=False),
                                  # PrintLayer(),
                                  PostionalEcnoder(dim_embd, dropout=dr_rate, time_steps=time_stp),
                                  memoTransormer(dim_embd, heads=encoder_head, layers=encoder_stack, actv='gelu'),
                                  # PrintLayer(),
                                  nn.Flatten(),
                                  # PrintLayer(),
                                  # 20480 is frame numbers * dim
                                  nn.Linear(time_stp * dim_embd, mid_layer),
                                  nn.Dropout(mid_drop),
                                  nn.ReLU(),
                                  nn.Linear(mid_layer, num_classes)

                                  )

    return model
