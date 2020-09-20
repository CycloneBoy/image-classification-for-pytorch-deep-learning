from .registry import is_model, is_model_in_modules, model_entrypoint
from .backbone.helpers import load_checkpoint
from .layers import set_layer_config, get_act_layer
from .layers import get_norm

from icdl.utils import comm


def create_model(
        model_name,
        pretrained=False,
        num_classes=1000,
        in_chans=3,
        checkpoint_path='',
        scriptable=None,
        exportable=None,
        no_jit=None,
        **kwargs):
    """Create a model

    Args:
        model_name (str): name of model to instantiate
        pretrained (bool): load pretrained ImageNet-1k weights if true
        num_classes (int): number of classes for final fully connected layer (default: 1000)
        in_chans (int): number of input channels / colors (default: 3)
        checkpoint_path (str): path of checkpoint to load after model is initialized
        scriptable (bool): set layer config so that model is jit scriptable (not working for all models yet)
        exportable (bool): set layer config so that model is traceable / ONNX exportable (not fully impl/obeyed yet)
        no_jit (bool): set layer config so that model doesn't utilize jit scripted layers (so far activations only)

    Keyword Args:
        drop_rate (float): dropout rate for training (default: 0.0)
        global_pool (str): global pool type (default: 'avg')
        **: other kwargs are model specific
    """
    model_args = dict(pretrained=pretrained, num_classes=num_classes, in_chans=in_chans)

    # Only EfficientNet and MobileNetV3 models have support for batchnorm params or drop_connect_rate passed as args
    is_efficientnet = is_model_in_modules(model_name, ['efficientnet', 'mobilenetv3'])
    if not is_efficientnet:
        kwargs.pop('bn_tf', None)
        kwargs.pop('bn_momentum', None)
        kwargs.pop('bn_eps', None)

        with set_layer_config(scriptable=scriptable, exportable=exportable, no_jit=no_jit):
            # activation layer
            act_layer = kwargs.pop('act_layer', 'relu')
            kwargs['act_layer'] = get_act_layer(act_layer)

    # norm_layer
    gpus = comm.get_world_size()
    norm_layer = kwargs.pop('norm_layer', 'BN')
    if gpus > 1 and norm_layer not in ['SyncBN', 'nnSyncBN', 'naiveSyncBN']:
        print("conver norm_layer {} to SyncBN".format(norm_layer))
        norm_layer = 'SyncBN'
    kwargs['norm_layer'] = get_norm(norm_layer)

    # handle backwards compat with drop_connect -> drop_path change
    drop_connect_rate = kwargs.pop('drop_connect_rate', None)
    if drop_connect_rate is not None and kwargs.get('drop_path_rate', None) is None:
        print("WARNING: 'drop_connect' as an argument is deprecated, please use 'drop_path'."
              " Setting drop_path to %f." % drop_connect_rate)
        kwargs['drop_path_rate'] = drop_connect_rate

    # Parameters that aren't supported by all models or are intended to only override model defaults if set
    # should default to None in command line args/cfg. Remove them if they are present and not set so that
    # non-supporting models don't break and default args remain in effect.
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    with set_layer_config(scriptable=scriptable, exportable=exportable, no_jit=no_jit):
        if is_model(model_name):
            create_fn = model_entrypoint(model_name)
            model = create_fn(**model_args, **kwargs)
        else:
            raise RuntimeError('Unknown model (%s)' % model_name)

    if checkpoint_path:
        load_checkpoint(model, checkpoint_path)

    return model


def build_backbone(cfg):
    scriptable = False
    exportable = False
    no_jit = False

    if cfg.MODEL.OPT == "infer":
        no_jit = True
        scriptable = True
    elif cfg.MODEL.OPT == "jit":
        no_jit = False
        scriptable = True

    return create_model(cfg.MODEL.BACKBONE,
                        True,
                        cfg.MODEL.CLASSES,
                        cfg.TRAIN.INPUT_CHANNEL,
                        cfg.MODEL.BACKBONE_WEIGHTS,
                        scriptable,
                        exportable,
                        no_jit,
                        norm_layer=cfg.MODEL.NORM_LAYER,
                        act_layer=cfg.MODEL.ACTIVATE,
                        global_pool=cfg.MODEL.POOLING_LAYER)