# This code was adapted from lucidrains existing `x-transformers` repository.
from simple_diffusion_model import Model
from simple_diffusion_model import DiffusionWrapper

import tqdm
import time
import wandb
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import torchvision.transforms as transforms
import numpy as np

import torch_fidelity
from torchvision.datasets import CIFAR10
from torch.utils.data import DataLoader

# constants

NUM_BATCHES = int(1e5)
BATCH_SIZE = 4
GRADIENT_ACCUMULATE_EVERY = 4
LEARNING_RATE = 1e-4
VALIDATE_EVERY  = 100
GENERATE_EVERY  = 500
EVALUATE = False
EVALUATE_EVERY  = 100000
EVALUATE_BATCH_SIZE = 50

# helpers

def cycle(loader):
    while True:
        for data in loader:
            yield data

def scale(x):
    return x * 2 - 1

def rescale(x):
    return (x + 1) / 2

class FidelityWrapper(nn.Module):
    def __init__(self, generator):
        super().__init__()
        self.generator = generator

    def forward(self, z):
        out = self.generator.generate(len(z))
        return rescale(out).mul(255).round().clamp(0, 255).to(torch.uint8)

def train():
    wandb.init(project="simple-diffusion-model")

    model = DiffusionWrapper(Model(), input_shape=(3, 32, 32))
    model.cuda()

    train_dataset = CIFAR10(root='/ailab_mat/personal/heo_yunjae/datasets/cifar10/', train=True, transform=transforms.ToTensor(), download=True)
    val_dataset = CIFAR10(root='/ailab_mat/personal/heo_yunjae/datasets/cifar10/', train=False, transform=transforms.ToTensor(), download=True)
    train_loader  = cycle(DataLoader(train_dataset, batch_size = BATCH_SIZE, num_workers=2))
    val_loader    = cycle(DataLoader(val_dataset, batch_size = BATCH_SIZE, num_workers=2))

    # optimizer

    optim = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)

    # training

    for i in tqdm.tqdm(range(NUM_BATCHES), mininterval=10., desc='training'):
        start_time = time.time()
        model.train()

        for __ in range(GRADIENT_ACCUMULATE_EVERY):
            batch, _ = next(train_loader)
            loss = model(scale(batch))
            loss.backward()

        end_time = time.time()
        print(f'training loss: {loss.item()}')
        train_loss = loss.item()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
        optim.step()
        optim.zero_grad()


        if i % VALIDATE_EVERY == 0:
            model.eval()
            with torch.no_grad():
                batch, _ = next(val_loader)
                loss = model(scale(batch))
                print(f'validation loss: {loss.item()}')
                val_loss = loss.item()

        if i % GENERATE_EVERY == 0:
            model.eval()
            samples = model.generate(1)
            image_array = rescale(samples)
            images = wandb.Image(image_array, caption="Generated")
            wandb.log({"examples": images}, commit=False)

        logs = {}

        logs = {
          **logs,
          'iter': i,
          'step_time': end_time - start_time,
          'train_loss': train_loss,
          'val_loss': val_loss,
        }

        wandb.log(logs)

        if EVALUATE:
            if (i % EVALUATE_EVERY == 0 and i != 0) or i == NUM_BATCHES - 1:
                model.eval()
                with torch.no_grad():
                    wrapped_inner = FidelityWrapper(model)
                    wrapped = torch_fidelity.GenerativeModelModuleWrapper(wrapped_inner,
                                                                          1, 'normal', 0)
                    metrics = torch_fidelity.calculate_metrics(input1=wrapped,
                                                               input1_model_num_samples=10000,
                                                               input2='cifar10-train',
                                                               batch_size=EVALUATE_BATCH_SIZE,
                                                               fid=True,
                                                               verbose=True)
                    wandb.log({"fid": metrics['frechet_inception_distance']})

    wandb.finish()

if __name__ == '__main__':
    train()
