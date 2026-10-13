import torch

from rvc.train.utils import replace_keys_in_dict


def load_fine_tune_weights(generator, discriminator, generator_path, discriminator_path):
    checkpoints = [torch.load(path, map_location="cpu", weights_only=True, mmap=True)
                   for path in (generator_path, discriminator_path)]
    epochs = [checkpoint.get("iteration") for checkpoint in checkpoints]
    if epochs[0] != epochs[1] or not isinstance(epochs[0], int) or epochs[0] < 1:
        raise ValueError("Fine-tuning requires a generator/discriminator pair from the same epoch.")
    for network, checkpoint in zip((generator, discriminator), checkpoints):
        weights = replace_keys_in_dict(checkpoint["model"], ".weight_v", ".parametrizations.weight.original1")
        weights = replace_keys_in_dict(weights, ".weight_g", ".parametrizations.weight.original0")
        target = network.module if hasattr(network, "module") else network
        # Unlike base pretraining, keep the learned speaker embedding and require every weight.
        target.load_state_dict(weights, strict=True)
    print(f"Fine-tuning personal G/D from epoch {epochs[0]}; speaker embedding preserved; fresh optimizers.")
    return epochs[0]
