
from .detector import *
from .real_detector import RealDetector
from . import img
from . import bb
from . import sub 
from . import rescalc
from . import datasets
# Datasets
from . import voc
from . import uiuc
from . import inria
from . import custom
from .beta_mixture import BetaMixture, binary_search # Temporarily exposed
from . import parallel 
from . import gradients
from . import plot
from . import imfilter
from . import matrix

from .core import *

from .ndfeature import ndfeature


from .binary_descriptor import *
from . import edge_descriptor
from . import parts_descriptor
from . import binary_tree_parts_descriptor
from . import polarity_parts_descriptor
from . import oriented_parts_descriptor
from . import binary_hog_descriptor

from .real_descriptor import *
from . import hog_descriptor
from . import real_parts_descriptor
from . import real_binary_tree_parts_descriptor
from . import real_polarity_parts_descriptor
from . import real_oriented_parts_descriptor

# TODO: Put somewhere better

def load_descriptor(settings):
    des_name = settings['detector']['descriptor']
    descriptor_filename = settings[des_name].get('file')
    detector_class = gv.Detector.getclass(settings['detector'].get('type', 'binary'))
    descriptor_cls = detector_class.DESCRIPTOR.getclass(des_name)
    if descriptor_filename is None:
        # If there is no descriptor filename, we'll just build it from the settings
        descriptor = descriptor_cls.load_from_dict(settings[des_name])
    else:
        descriptor = descriptor_cls.load(descriptor_filename)
    return descriptor


def load_binary_descriptor(settings):
    return load_descriptor(gv.BinaryDescriptor, settings)

def load_real_descriptor(settings):
    return load_descriptor(gv.RealDescriptor, settings)
