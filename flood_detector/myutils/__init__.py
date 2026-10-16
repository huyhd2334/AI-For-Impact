import sys
import torch
import torch.nn

# Backward compatibility shim for pickled segmentation models
try:
    import segmentation_models_pytorch as smp
    if hasattr(smp, 'decoders') and hasattr(smp.decoders, 'linknet'):
        sys.modules.setdefault('segmentation_models_pytorch.linknet', smp.decoders.linknet)
        sys.modules.setdefault('segmentation_models_pytorch.linknet.model', smp.decoders.linknet.model)
except ImportError:
    pass

try:
    import efficientnet_pytorch.utils
    if not hasattr(efficientnet_pytorch.utils, 'Identity'):
        efficientnet_pytorch.utils.Identity = torch.nn.Identity
except ImportError:
    pass

from .data import *
from .plot_depth import Visualizer
from .system import *