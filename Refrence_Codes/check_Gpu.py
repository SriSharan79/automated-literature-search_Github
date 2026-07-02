import torch
print(torch.cuda.is_available())
print(torch.__version__)
print(torch.version.cuda) # This will tell you the CUDA version PyTorch was built with
