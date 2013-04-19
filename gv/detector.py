
import matplotlib.pylab as plt

import amitgroup as ag
import numpy as np
import scipy.signal
from .saveable import Saveable
import gv
import sys
from copy import deepcopy

def offset_img(img, off):
    sh = img.shape
    if sh == (0, 0):
        return img
    else:
        x = np.zeros(sh)
        x[max(off[0], 0):min(sh[0]+off[0], sh[0]), \
          max(off[1], 0):min(sh[1]+off[1], sh[1])] = \
            img[max(-off[0], 0):min(sh[0]-off[0], sh[0]), \
                max(-off[1], 0):min(sh[1]-off[1], sh[1])]
        return x

def _integrate(ii, r0, c0, r1, c1):
    """Use an integral image to integrate over a given window.

    Parameters
    ----------
    ii : ndarray
    Integral image.
    r0, c0 : int
    Top-left corner of block to be summed.
    r1, c1 : int
    Bottom-right corner of block to be summed.

    Returns
    -------
    S : int
    Integral (sum) over the given window.

    """
    # This line is modified
    S = np.zeros(ii.shape[-1]) 

    S += ii[r1, c1]

    if (r0 - 1 >= 0) and (c0 - 1 >= 0):
        S += ii[r0 - 1, c0 - 1]

    if (r0 - 1 >= 0):
        S -= ii[r0 - 1, c1]

    if (c0 - 1 >= 0):
        S -= ii[r1, c0 - 1]

    return S

class Detector(Saveable):
    """
    An object detector representing a single class (although mutliple mixtures of that class).
        
    It uses the BinaryDescriptor as feature extractor, and then runs a mixture model on top of that.
    """
    def __init__(self, num_mixtures, descriptor, settings={}):
        assert isinstance(descriptor, gv.BinaryDescriptor)
        self.descriptor = descriptor 
        self.num_mixtures = num_mixtures
        self.mixture = None
        self.log_kernels = None
        self.log_invkernels = None
        self.kernel_basis = None
        self.kernel_basis_samples = None
        self.kernel_templates = None
        self.support = None

        self.use_alpha = None

        self.settings = {}
        self.settings['scale_factor'] = np.sqrt(2)
        self.settings['bounding_box_opacity_threshold'] = 0.1
        self.settings['min_probability'] = 0.05
        self.settings['subsample_size'] = (8, 8)
        self.settings['train_unspread'] = True
        self.settings['min_size'] = 75
        self.settings['max_size'] = 450
        self.settings.update(settings)
    
    def copy(self):
        return deepcopy(self)

    @property
    def train_unspread(self):
        return self.settings['train_unspread']

    @property
    def num_features(self):
        return self.descriptor.num_features
    
    @property
    def use_basis(self):
        return self.kernel_basis is not None

    def load_img(self, images, offsets=None):
        resize_to = self.settings.get('image_size')
        for i, img_obj in enumerate(images):
            if isinstance(img_obj, str):
                print("Image file name", img_obj)
                img = gv.img.load_image(img_obj)
            grayscale_img = gv.img.asgray(img)

            # Resize the image before extracting features
            if resize_to is not None and resize_to != grayscale_img.shape[:2]:
                img = gv.img.resize(img, resize_to)
                grayscale_img = gv.img.resize(grayscale_img, resize_to) 

            # Offset the image
            if offsets is not None:
                grayscale_img = offset_img(grayscale_img, offsets[i])
                img = offset_img(img, offsets[i])

            # Now, binarize the support in a clever way (notice that we have to adjust for pre-multiplied alpha)
            alpha = (img[...,3] > 0.2)

            eps = sys.float_info.epsilon
            imrgb = (img[...,:3]+eps)/(img[...,3:4]+eps)
            
            new_img = imrgb * alpha.reshape(alpha.shape+(1,))

            new_grayscale_img = new_img[...,:3].mean(axis=-1)

            yield i, grayscale_img, img, alpha

    def gen_img(self, images, actual=False):
        for i, grayscale_img, img, alpha in self.load_img(images):
            if self.train_unspread and not actual:
                final_edges = self.extract_unspread_features(grayscale_img)
            else:
                final_edges = self.extract_spread_features(grayscale_img)
                if actual:
                    final_edges = self.subsample(final_edges)
            yield final_edges

    def train_from_images(self, images):
        self.orig_kernel_size = None

        mixture, kernel_templates, support = self._train(images)

        self.mixture = mixture
        self.kernel_templates = kernel_templates
        self.support = support
            
        self._preprocess()

    def _train(self, images, offsets=None):
        self.use_alpha = None
        
        real_shape = None
        shape = None
        output = None
        final_output = None
        alpha_maps = None 
        sparse = False # TODO: Change
        build_sparse = True and sparse
        feats = None

        # TODO: Remove
        orig_output = None
        psize = self.settings['subsample_size']

        for i, grayscale_img, img, alpha in self.load_img(images, offsets):
            ag.info(i, "Processing image", i)
            if self.use_alpha is None:
                self.use_alpha = (img.ndim == 3 and img.shape[-1] == 4)
                if self.use_alpha:
                    alpha_maps = np.empty((len(images),) + img.shape[:2], dtype=np.uint8)

            if self.use_alpha:
                a = (img[...,3] > 0.05).astype(np.uint8)
                alpha_maps[i] = a

            if self.train_unspread:
                final_edges = self.extract_unspread_features(grayscale_img)
            else:
                final_edges = self.extract_spread_features(grayscale_img)

            orig_edges = self.extract_spread_features(grayscale_img)
            edges = gv.sub.subsample(orig_edges, (2, 2)).ravel()

            if self.orig_kernel_size is None:
                self.orig_kernel_size = (img.shape[0], img.shape[1])
        
            # Extract the parts, with some pooling 
            if shape is None:
                shape = edges.shape
                if sparse:
                    if build_sparse:
                        output = scipy.sparse.dok_matrix((len(images),) + edges.shape, dtype=np.uint8)
                    else:
                        output = np.zeros((len(images),) + edges.shape, dtype=np.uint8)
                else:
                    output = np.empty((len(images),) + edges.shape, dtype=np.uint8)

                orig_output = np.empty((len(images),) + orig_edges.shape, dtype=np.uint8)
            
            orig_output[i] = orig_edges
                
            if build_sparse:
                for j in np.where(edges==1):
                    output[i,j] = 1
            else:
                output[i] = edges

        ag.info("Running mixture model in Detector")

        if output is None:
            raise Exception("Found no training images")

        if build_sparse:
            output = output.tocsr()
        elif sparse:
            output = scipy.sparse.csr_matrix(output)
        else:
            output = np.asmatrix(output)

        # Train mixture model OR SVM
        mixture = ag.stats.BernoulliMixture(self.num_mixtures, output, float_type=np.float32)

        minp = 1e-5
        mixture.run_EM(1e-8, minp)

        #mixture.templates = np.empty(0)

        # Now create our unspread kernels
        # Remix it - this iterable will produce each object and then throw it away,
        # so that we can remix without having to ever keep all mixing data in memory at once
             
        kernel_templates = np.clip(mixture.remix_iterable(self.gen_img(images)), 1e-5, 1-1e-5)

        # Pick out the support, by remixing the alpha channel
        if self.use_alpha: #TODO: Temp2
            support = mixture.remix(alpha_maps).astype(np.float32)
            # TODO: Temporary fix
            self.full_support = support
            support = support[:,6:-6,6:-6]
        else:
            support = None#np.ones((self.num_mixtures,) + shape[:2])

        # TODO: Figure this out.
        self.support = support


        # Determine the log likelihood of the training data
        fix_bkg = self.settings.get('fixed_background_probability')
        radii = self.settings['spread_radii']
        if fix_bkg is not None:
            #flat = output.reshape((output.shape[0], -1))
            #flat_template = mixture.kernel_templates.reshape((mixture.num_mixtures, -1)) 

            if 0:
                bkg_llh = output.sum(axis=1) * np.log(bkg) + (1-output).sum(axis=1) * np.log(1-bkg)
                 
                lrt = mixture.mle - bkg_llh

                self.fixed_train_std = lrt.std()
                self.fixed_train_mean = lrt.mean()

                llhs = np.zeros((mixture.num_mix, output.shape[0]))
                kernel_templates.shape[0]
                for k in xrange(mixture.num_mix):
                    for i in xrange(output.shape[0]):
                        kern = kernel_templates[k].ravel()
                        X = orig_output[i].ravel()
                        llhs[k,i] = np.sum( X * np.log(kern/bkg) + (1-X) * np.log((1-kern)/(1-bkg)) )


            L = len(self.settings['levels'])
            self.fixed_train_mean = np.zeros(L)
            self.fixed_train_std = np.zeros(L)

            for i, (sub, spread) in enumerate(self.settings['levels']):

                psize = (sub,)*2
                radii = (spread,)*2

                self.settings['subsample_size'] = psize
                self.settings['spread_radii'] = radii 

                orig_output = None
                # Get images with the right amount of spreading
                for j, grayscale_img, img, alpha in self.load_img(images, offsets):
                    orig_edges = self.extract_spread_features(grayscale_img, settings=dict(spread_radii=radii))
                    if orig_output is None:
                        orig_output = np.empty((len(images),) + orig_edges.shape, dtype=np.uint8)
                    orig_output[j] = orig_edges
                        #orig_edges = self.extract_spread_features(grayscale_img)
                    

                bkg = 1 - (1 - fix_bkg)**((2 * radii[0] + 1) * (2 * radii[1] + 1))
                #bkg = 0.05

                self.kernel_templates = kernel_templates
                # TODO: This gives a spread background!
                kernels = self.prepare_kernels(np.ones(kernel_templates.shape[-1])*bkg, settings=dict(spread_radii=radii, subsample_size=psize))

                sub_output = gv.sub.subsample(orig_output, psize, skip_first_axis=True)

                #import pylab as plt
                #plt.imshow(kernels[0].sum(axis=-1), interpolation='nearest')
                #plt.show()

                #print('sub_output', sub_output.shape)
                theta = kernels.reshape((kernels.shape[0], -1))
                X = sub_output.reshape((sub_output.shape[0], -1))

                try:
                    llhs = np.dot(X, np.log(theta/(1-theta) * ((1-bkg)/bkg)).T)
                except:
                    import pdb; pdb.set_trace()
                #C = np.log((1-theta)/(1-bkg)).sum(axis=1)
                #llhs += C
                
                lrt = llhs.max(axis=1)

                self.fixed_train_mean[i] = lrt.mean()
                self.fixed_train_std[i] = lrt.std()

#
            print("mean", self.fixed_train_mean)
            print("std", self.fixed_train_std)
                
        
    
        return mixture, kernel_templates, support

    def extract_unspread_features(self, image, support_mask=None):
        edges = self.descriptor.extract_features(image, {'spread_radii': (0, 0)}, support_mask=support_mask)
        return edges

    def extract_spread_features(self, image, settings={}):

        #self.back = image.sum(axis=0).sum(axis=0) / np.prod(image.shape[:2])

        sett = self.settings.copy()
        sett.update(settings)
    
        if 0:
            th = 0
            N = 100
            p = 0.05
            
            for n in xrange(N):
                p2 = 1 - np.sum([scipy.misc.comb(N, i) * p**i * (1-p)**(N-i) for i in xrange(n)])
                if p2 < 0.01:
                    th = n
                    break

            print('Threshold', th)

        edges = self.descriptor.extract_features(image, {'spread_radii': sett['spread_radii']})
        return edges 

    @property
    def unpooled_kernel_size(self):
        return (self.kernel_templates.shape[1], self.kernel_templates.shape[2])

    @property
    def unpooled_kernel_side(self):
        return max(self.unpooled_kernel_size)

    def bkg_model(self, edges, location=None):
        """
        Returns background model in three different ways:

        * As a (num_features,) long vector, valid for the 
          entire image

        * As a (size0, size1, num_features) with a separate 
          background model for each pixel

        * As a (obj_size0, obj_size1, num_features) for a 
          separate background for each pixel inside the
          object. If location = True, then this format will
          be used.

        """
        bkg_type = self.settings.get('bkg_type')

        if bkg_type == 'constant':
            bkg_value = self.settings['fixed_bkg']
            return np.ones(self.num_features) * bkg_value 

        elif bkg_type == 'from-file':
            return self.fixed_bkg

        elif bkg_type == 'per-image-average':
            bkg = edges.reshape((-1, self.num_features)).mean(axis=0)
            #eps = self.settings['min_probability']
            eps = 1e-8
            return np.clip(bkg, eps, 1 - eps)

        elif bkg_type == 'smoothed':
            pass

        else:
            raise ValueError("Specified background model not available")
    def subsample(self, edges):
        return gv.sub.subsample(edges, self.settings['subsample_size'])

    def prepare_kernels(self, back, settings={}):
        sett = self.settings.copy()
        sett.update(settings) 

        if not self.use_basis:
            kernels = self.kernel_templates.copy()

        eps = sett['min_probability']
        psize = sett['subsample_size']

        if self.train_unspread:
            radii = sett['spread_radii']
            neighborhood_area = ((2*radii[0]+1)*(2*radii[1]+1))
            #back = np.load('bkg.npy')
            #nospread_back = 1 - (1 - back)**(1/neighborhood_area)
            nospread_back = back
            print back.min(), back.max()
            
            # TODO: Use this background instead.
            #nospread_back = np.load('bkg.npy')
            #nospread_back = np.ones(self.num_features) * 0.003

            if self.use_basis:
                C = self.kernel_basis * np.expand_dims(nospread_back, -1)
                kernels = C.sum(axis=-2) / self.kernel_basis_samples

            print self.kernel_basis_samples
            print kernels.min(), kernels.max()

            aa_log = np.log(1 - kernels)
            aa_log = ag.util.multipad(aa_log, (0, radii[0], radii[1], 0), np.log(1-nospread_back))
            #aa_log = ag.util.zeropad(aa_log, (0, radii[0], radii[1], 0))
            integral_aa_log = aa_log.cumsum(1).cumsum(2)

            offsets = gv.sub.subsample_offset(kernels[0], psize)

            # Fix kernels
            istep = 2*radii[0]
            jstep = 2*radii[1]
            sh = kernels.shape[1:3]
            for mixcomp in xrange(self.num_mixtures):
                # Note, we are going in strides of psize, given a certain offset, since
                # we will be subsampling anyway, so we don't need to do the rest.
                for i in xrange(offsets[0], sh[0], psize[0]):
                    for j in xrange(offsets[1], sh[1], psize[1]):
                        p = _integrate(integral_aa_log[mixcomp], i, j, i+istep, j+jstep)
                        kernels[mixcomp,i,j] = 1 - np.exp(p)


        # Subsample kernels
        sub_kernels = gv.sub.subsample(kernels, psize, skip_first_axis=True)

        sub_kernels = np.clip(sub_kernels, eps, 1-eps)

        K = self.settings.get('quantize_bins')
        if K is not None:
            sub_kernels = np.round(1+sub_kernels*(K-2))/K

        return sub_kernels

    def detect_coarse_single_factor(self, img, factor, mixcomp, img_id=0):
        """
        TODO: Experimental changes under way!
        """

        from skimage.transform import pyramid_reduce
        if abs(factor-1) < 1e-8:
            img_resized = img
        else:
            img_resized = pyramid_reduce(img, downscale=factor)

        last_resmap = None

        sold = self.settings.copy()

        resmaps = []

            
        self.settings = sold
        psize = self.settings['subsample_size']
        radii = self.settings['spread_radii']

        def extract(image):
            return self.descriptor.extract_features(image, dict(spread_radii=radii, preserve_size=True))
        def extract2(image):
            return self.descriptor.extract_features(image, dict(spread_radii=(0, 0), preserve_size=True))

        # Last psize

        up_feats = extract(img_resized)
        unspread_feats = extract2(img_resized)
        bkg = self.bkg_model(unspread_feats)
        feats = gv.sub.subsample(up_feats, psize) 
        print bkg.sum()
        sub_kernels = self.prepare_kernels(bkg, settings=dict(spread_radii=radii, subsample_size=psize))

        print "Running here"
        bbs, resmap = self.detect_coarse_at_factor(feats, sub_kernels, bkg, factor, mixcomp, resmaps=resmaps)

        final_bbs = bbs

        return final_bbs, resmap, feats, img_resized

    def detect_coarse(self, img, fileobj=None, mixcomps=None):
        if mixcomps is None:
            mixcomps = range(self.num_mixtures)

        # TODO: Temporary stuff
        if 1:
            bbs = []
            for mixcomp in mixcomps:
                bbs0, resmap, feats, img_resized = self.detect_coarse_single_factor(img, 1.0, mixcomp, img_id=fileobj.img_id)
                bbs += bbs0
            #bbs2, resmap, feats, img_resized = self.detect_coarse_single_factor(img, 1.0, 1, img_id=fileobj.img_id)

            # Do NMS here
            final_bbs = self.nonmaximal_suppression(bbs)
            
            # Mark corrects here
            if fileobj is not None:
                self.label_corrects(final_bbs, fileobj)


            return final_bbs
        

        # Build image pyramid
        min_size = self.settings['min_size'] 
        min_factor = min_size / max(self.orig_kernel_size)#self.unpooled_kernel_side

        max_size = self.settings['max_size'] 
        max_factor = max_size / max(self.orig_kernel_size)#self.unpooled_kernel_side

        num_levels = 2
        factors = []
        skips = 0
        eps = 1e-8
        for i in xrange(1000):
            factor = self.settings['scale_factor']**i
            if factor > max_factor+eps:
                break
            if factor >= min_factor-eps:
                factors.append(factor) 
            else:
                skips += 1
        num_levels = len(factors) + skips

        ag.set_verbose(False)
        ag.info("Setting up pyramid")
        from skimage.transform import pyramid_gaussian 
        pyramid = list(pyramid_gaussian(img, max_layer=num_levels, downscale=self.settings['scale_factor']))[skips:]

        # Filter out levels that are below minimum scale

        # Prepare each level 
        def extract(image):
            return self.descriptor.extract_features(image, dict(spread_radii=self.settings['spread_radii'], preserve_size=True))

        #edge_pyramid = map(self.extract_spread_features, pyramid)
        ag.info("Getting edge pyramid")
        edge_pyramid = map(extract, pyramid)
        ag.info("Extract background model")
        bkg_pyramid = map(self.bkg_model, edge_pyramid)
        ag.info("Subsample")
        small_pyramid = map(self.subsample, edge_pyramid) 

        bbs = []
        for i, factor in enumerate(factors):
            # Prepare the kernel for this mixture component
            ag.info("Prepare kernel", i, "factor", factor)
            sub_kernels = self.prepare_kernels(bkg_pyramid[i][0])

            for mixcomp in mixcomps:
                ag.info("Detect for mixture component", mixcomp)
            #for mixcomp in [1]:
                bbsthis, _ = self.detect_coarse_at_factor(small_pyramid[i], sub_kernels, bkg_pyramid[i][1], factor, mixcomp)
                bbs += bbsthis

        ag.info("Maximal suppression")
        # Do NMS here
        final_bbs = self.nonmaximal_suppression(bbs)
        
        # Mark corrects here
        if fileobj is not None:
            self.label_corrects(final_bbs, fileobj)


        return final_bbs

    def detect_coarse_at_factor(self, sub_feats, sub_kernels, back, factor, mixcomp, resmaps=None):
        # Get background level
        resmap = self.response_map(sub_feats, sub_kernels, back, mixcomp, level=-1)

        #print('resmap', resmap.shape)

        # TODO: Remove edges
        sh = sub_kernels.shape[1:3]
        #resmap2 = resmap.min()*np.ones(resmap.shape)
        #resmap2[sh[0]//2:-sh[0]//2, sh[1]//2:-sh[1]//2] = resmap[sh[0]//2:-sh[0]//2, sh[1]//2:-sh[1]//2]
        #resmap = resmap2

        #resmap /= self.means[mixcomp]

        #prin

        th = -np.inf
        #top_th = resmap.max()#200.0
        top_th = 200.0
        bbs = []

        #nn_resmaps = np.zeros((2,) + resmap.shape)
        #if resmaps is not None:
        #    nn_resmaps[0] = ag.util.nn_resample2d(resmaps[0], resmap.shape)
        #    nn_resmaps[1] = ag.util.nn_resample2d(resmaps[1], resmap.shape)
        
        psize = self.settings['subsample_size']
        agg_factors = tuple([psize[i] * factor for i in xrange(2)])
        bb_bigger = (0.0, 0.0, sub_feats.shape[0] * agg_factors[0], sub_feats.shape[1] * agg_factors[1])
        for i in xrange(resmap.shape[0]):
            for j in xrange(resmap.shape[1]):
                score = resmap[i,j]
                if score >= th:
                    #ix = i * psize[0]
                    #iy = j * psize[1]

                    i_corner = i-sub_kernels.shape[1]//2
                    j_corner = j-sub_kernels.shape[2]//2

                    obj_bb = self.boundingboxes[mixcomp]
                    bb = [(i_corner + obj_bb[0]) * agg_factors[0],
                          (j_corner + obj_bb[1]) * agg_factors[1],
                          (i_corner + obj_bb[2]) * agg_factors[0],
                          (j_corner + obj_bb[3]) * agg_factors[1],
                    ]

                    # Clip to bb_bigger 
                    bb = gv.bb.intersection(bb, bb_bigger)
    
                    score0 = score1 = 0

                    conf = score
                    dbb = gv.bb.DetectionBB(score=score, score0=score0, score1=score1, box=bb, confidence=conf, scale=factor, mixcomp=mixcomp)

                    if gv.bb.area(bb) > 0:
                        bbs.append(dbb)

        # Let's limit to five per level
        bbs_sorted = self.nonmaximal_suppression(bbs)
        bbs_sorted = bbs_sorted[:5]

        return bbs_sorted, resmap

    def response_map(self, sub_feats, sub_kernels, back, mixcomp, level=0):
        sh = sub_kernels.shape
        padding = (sh[1]//2, sh[2]//2, 0)
        bigger = ag.util.zeropad(sub_feats, padding)
        
        res = None

        # With larger kernels, the fftconvolve is much faster. However,
        # this is not the case for smaller kernels.
        from .fast import multifeature_correlate2d

        kern = sub_kernels[mixcomp]
        weights = np.log(kern/(1-kern) * ((1-back)/back))

        res = multifeature_correlate2d(bigger, weights) 

        #print("level", level, self.train_mean[level])
        testing_type = self.settings.get('testing_type', 'object-model')
        if testing_type == 'fixed':
            res -= self.fixed_train_mean[level]
            res /= self.fixed_train_std[level]
        elif testing_type == 'object-model':
            assert self.num_mixtures == 1, "Need to standardize!"
            a = weights
            print '---------'
            print res.max()
            res -= (kern * a).sum()
            res /= np.sqrt((a**2 * kern * (1 - kern)).sum())
            print (kern * a).sum()
            print np.sqrt((a**2 * kern * (1 - kern)).sum())
            print res.max()
        elif testing_type == 'background-model':
            assert self.num_mixtures == 1, "Need to standardize!"
            a = weights
            res -= (back * a).sum()
            res /= np.sqrt((a**2 * back * (1 - back)).sum())

        return res

    def nonmaximal_suppression(self, bbs):
        # This one will respect scales a bit more
        bbs_sorted = sorted(bbs, reverse=True)

        overlap_threshold = 0.5

        # Suppress within a radius of H neighboring scale factors
        sf = self.settings['scale_factor']
        H = self.settings.get('scale_suppress_radius', 1)
        i = 1
        lo, hi = 1/(H*sf)-0.01, H*sf+0.01
        while i < len(bbs_sorted):
            # TODO: This can be vastly improved performance-wise
            area_i = gv.bb.area(bbs_sorted[i].box)
            for j in xrange(i):
                overlap = gv.bb.area(gv.bb.intersection(bbs_sorted[i].box, bbs_sorted[j].box))/area_i
                scale_diff = (bbs_sorted[i].scale / bbs_sorted[j].scale)
                if overlap > overlap_threshold and \
                   lo <= scale_diff <= hi: 
                    del bbs_sorted[i]
                    i -= 1
                    break

            i += 1
        return bbs_sorted

    def bounding_box_for_mix_comp(self, k):
        """This returns a bounding box of the support for a given component"""

        # Take the bounding box of the support, with a certain threshold.
        #print("Using alpha", self.use_alpha, "support", self.support)
        if self.use_alpha and self.support is not None:
            supp = self.support[k] 
            supp_axs = [supp.max(axis=1-i) for i in xrange(2)]

            th = self.settings['bounding_box_opacity_threshold']
            # Check first and last value of that threshold
            bb = [np.where(supp_axs[i] > th)[0][[0,-1]] for i in xrange(2)]

            # This bb looks like [(x0, x1), (y0, y1)], when we want it as (x0, y0, x1, y1)
            psize = self.settings['subsample_size']
            ret = (bb[0][0]/psize[0], bb[1][0]/psize[1], bb[0][1]/psize[0], bb[1][1]/psize[1])
            return ret
        else:
            psize = self.settings['subsample_size']
            return (0, 0, self.orig_kernel_size[0]/psize[0], self.orig_kernel_size[1]/psize[1])

    def label_corrects(self, bbs, fileobj):
        used_bb = set([])
        tot = 0
        for bb2obj in bbs:
            bb2 = bb2obj.box
            best_score = None
            best_bbobj = None
            best_bb = None
            for bb1obj in fileobj.boxes: 
                bb1 = bb1obj.box
                if bb1 not in used_bb:
                    #print('union_area', gv.bb.union_area(bb1, bb2))
                    #print('intersection_area', gv.bb.area(gv.bb.intersection(bb1, bb2)))
                    #print('here', gv.bb.fraction_metric(bb1, bb2))
                    score = gv.bb.fraction_metric(bb1, bb2)
                    if score >= 0.5:
                        if best_score is None or score > best_score:
                            best_score = score
                            best_bbobj = bb1obj 
                            best_bb = bb1

            if best_bbobj is not None:
                bb2obj.correct = True
                bb2obj.difficult = best_bbobj.difficult
                # Don't count difficult
                if not best_bbobj.difficult:
                    tot += 1
                used_bb.add(best_bb)

    def _preprocess(self):
        """Pre-processes things"""
        # Prepare bounding boxes for all mixture model
        self.boundingboxes = np.array([self.bounding_box_for_mix_comp(i) for i in xrange(self.num_mixtures)])

    @classmethod
    def load_from_dict(cls, d):
        try:
            num_mixtures = d['num_mixtures']
            descriptor_cls = gv.BinaryDescriptor.getclass(d['descriptor_name'])
            if descriptor_cls is None:
                raise Exception("The descriptor class {0} is not registered".format(d['descriptor_name'])) 
            descriptor = descriptor_cls.load_from_dict(d['descriptor'])
            obj = cls(num_mixtures, descriptor)
            mix_dict = d.get('mixture')
            if mix_dict is not None:
                obj.mixture = ag.stats.BernoulliMixture.load_from_dict(d['mixture'])
            else:
                obj.mixture = None
            obj.settings = d['settings']
            obj.orig_kernel_size = d.get('orig_kernel_size')
            obj.kernel_basis = d.get('kernel_basis')
            obj.kernel_basis_samples = d.get('kernel_basis_samples')
            obj.kernel_templates = d.get('kernel_templates')
            obj.use_alpha = d['use_alpha']
            obj.support = d.get('support')

            obj.fixed_train_std = d.get('fixed_train_std')
            obj.fixed_train_mean = d.get('fixed_train_mean')

            obj._preprocess()
            return obj
        except KeyError as e:
            # TODO: Create a new exception for these kinds of problems
            raise Exception("Could not reconstruct class from dictionary. Missing '{0}'".format(e))

    def save_to_dict(self):
        d = {}
        d['num_mixtures'] = self.num_mixtures
        d['descriptor_name'] = self.descriptor.name
        d['descriptor'] = self.descriptor.save_to_dict()
        if self.mixture is not None:
            d['mixture'] = self.mixture.save_to_dict(save_affinities=True)
        d['orig_kernel_size'] = self.orig_kernel_size
        d['kernel_templates'] = self.kernel_templates
        d['kernel_basis'] = self.kernel_basis
        d['kernel_basis_samples'] = self.kernel_basis_samples
        d['use_alpha'] = self.use_alpha
        d['support'] = self.support
        d['settings'] = self.settings

        if self.settings['testing_type'] == 'fixed':
            d['fixed_train_std'] = self.fixed_train_std
            d['fixed_train_mean'] = self.fixed_train_mean

        return d
