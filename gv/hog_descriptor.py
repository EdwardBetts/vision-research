from __future__ import division
import numpy as np
from ndfeature import ndfeature
from real_descriptor import RealDescriptor
from unraveled_hog import unraveled_hog

@RealDescriptor.register('hog')
class HOGDescriptor(RealDescriptor):
    def __init__(self, settings={}):
        self.settings = {}
        self.settings['cells_per_block'] = (3, 3)
        self.settings['pixels_per_cell'] = (6, 6)
        self.settings['orientations'] = 9 
        self.settings['normalise'] = True 
        
        self.settings['binarize_threshold'] = 0.02
        self.settings.update(settings)

    def extract_features(self, image, settings={}, raveled=True):
        from skimage import feature
        orientations = self.settings['orientations']
        ppc = self.settings['pixels_per_cell']
        hog = unraveled_hog(image, 
                          orientations=self.settings['orientations'],
                          pixels_per_cell=ppc,
                          cells_per_block=self.settings['cells_per_block'],
                          normalise=self.settings['normalise'])

        # Let's binarize the features somehow
        #hog = (hog > self.settings['binarize_threshold']).astype(np.uint8)

        if raveled:
            hog = hog.reshape(hog.shape[:2] + (-1,))

        # How much space was cut away?
        buf = tuple(image.shape[i] - hog.shape[i] * ppc[i] for i in xrange(2))
        lower = tuple(buf[i]//2 for i in xrange(2))
        upper = tuple(image.shape[i] - (buf[i]-lower[i]) for i in xrange(2))

        return ndfeature(hog, lower=lower, upper=upper)

    @property
    def num_features(self):
        return self.settings['orientations'] * np.prod(self.settings['cells_per_block'])

    def save_to_dict(self):
        return self.settings

    @classmethod
    def load_from_dict(cls, d):
        return cls(d)
