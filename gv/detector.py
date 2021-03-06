

import amitgroup as ag
import numpy as np
import scipy.signal
from .saveable import Saveable, SaveableRegistry
from .named_registry import NamedRegistry
from .binary_descriptor import BinaryDescriptor
import gv
import sys
from copy import deepcopy
import itertools as itr
from scipy.misc import logsumexp
from scipy.special import logit, expit

# TODO: Build into train_basis_...
#cad_kernels = np.load('cad_kernel.npy')

@NamedRegistry.root
#class Detector(Saveable, NamedRegistry):
class Detector(SaveableRegistry):
    DESCRIPTOR = None

    def __init__(self, num_mixtures, descriptor, settings={}):
        assert isinstance(descriptor, self.DESCRIPTOR), (descriptor, self.DESCRIPTOR)
        self.descriptor = descriptor 
        self.num_mixtures = num_mixtures

        self.settings = {}
        self.settings['scale_factor'] = np.sqrt(2)
        self.settings['bounding_box_opacity_threshold'] = 0.1
        self.settings['min_size'] = 75
        self.settings['max_size'] = 450

@Detector.register('binary')
class BernoulliDetector(Detector):
    DESCRIPTOR = BinaryDescriptor

    """
    An object detector representing a single class (although mutliple mixtures of that class).
        
    It uses the BinaryDescriptor as feature extractor, and then runs a mixture model on top of that.
    """
    def __init__(self, num_mixtures, descriptor, settings={}):
        super(BernoulliDetector, self).__init__(num_mixtures, descriptor, settings)
        self.mixture = None
        self.log_kernels = None
        self.log_invkernels = None
        self.kernel_basis = None
        self.kernel_basis_samples = None
        self.kernel_templates = None
        self.kernel_sizes = None
        self.support = None
        self.fixed_bkg = None
        self.fixed_spread_bkg = None
        self.bkg_mixture_params = None
        self.standardization_info = None
        self.standardization_info2 = None # TODO: New
        self.indices = None # TODO: Recently added, keeper?
        self._eps = None
        self._param = None

        self.indices2 = None
        self.clfs = None
        self.TEMP_second = False
        self.fixed_spread_bkg2 = None
        self.extra = {}

        self.use_alpha = None

        self.settings['subsample_size'] = (8, 8)
        self.settings['train_unspread'] = True
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
            print(img_obj)
            if isinstance(img_obj, str):
                img = gv.img.load_image(img_obj)
            grayscale_img = gv.img.asgray(img)

            # Resize the image before extracting features
            if self.settings.get('crop_image'):
                img = gv.img.crop(img, resize_to)
                gryscale_img = gv.img.crop(grayscale_img, resize_to)
            elif resize_to is not None and resize_to != grayscale_img.shape[:2]:
                img = gv.img.resize(img, resize_to)
                grayscale_img = gv.img.resize(grayscale_img, resize_to) 

            # Offset the image
            if offsets is not None:
                grayscale_img = gv.img.offset(grayscale_img, offsets[i])
                img = gv.img.offset(img, offsets[i])

            # Now, binarize the support in a clever way (notice that we have to adjust for pre-multiplied alpha)
            if img.ndim == 2:
                alpha = np.ones(img.shape)
            else:
                alpha = (img[...,3] > 0.2)

            #eps = sys.float_info.epsilon
            #imrgb = (img[...,:3]+eps)/(img[...,3:4]+eps)
            
            #new_img = imrgb * alpha.reshape(alpha.shape+(1,))

            #new_grayscale_img = new_img[...,:3].mean(axis=-1)

            yield i, grayscale_img, img, alpha

    def gen_img(self, images, actual=False):
        for i, grayscale_img, img, alpha in self.load_img(images):
            final_edges = self.extract_spread_features(grayscale_img)
            #final_edges = self.subsample(final_edges)
            yield final_edges

    def train_from_images(self, images, labels=None):
        #self.orig_kernel_size = None

        mixture, kernel_templates, kernel_sizes, support = self._train(images)

        self.mixture = mixture
        self.kernel_templates = kernel_templates
        self.kernel_sizes = kernel_sizes
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

        extra_bits = None

        for i, grayscale_img, img, alpha in self.load_img(images, offsets):
            ag.info(i, "Processing image", i)
            if self.use_alpha is None:
                self.use_alpha = (img.ndim == 3 and img.shape[-1] == 4)
                #if self.use_alpha:
                alpha_maps = np.empty((len(images),) + img.shape[:2], dtype=np.uint8)

            #if self.use_alpha:
            alpha_maps[i] = alpha

            edges_nonflat = self.extract_spread_features(grayscale_img)
            #edges_nonflat = gv.sub.subsample(orig_edges, psize)
            if shape is None:
                shape = edges_nonflat.shape

            edges = edges_nonflat.ravel()
            #binmap = gv.img.bounding_box_as_binary_map(alpha)
            #binmap = gv.sub.subsample(binmap, (3, 3))
            #binmap = (alpha > 0.5).astype(np.uint8)
            #flat_extra = binmap.ravel()
            flat_extra = np.zeros(1, dtype=np.uint8)
            #flat_extra = np.concatenate([flat_extra, flat_extra])
            edges = np.concatenate([edges_nonflat.ravel(), flat_extra])

            if extra_bits is None:  
                extra_bits = flat_extra.size

            #if self.orig_kernel_size is None:
                #self.orig_kernel_size = (img.shape[0], img.shape[1])
        
            # Extract the parts, with some pooling 
            if output is None:
                if sparse:
                    if build_sparse:
                        output = scipy.sparse.dok_matrix((len(images),) + edges.shape, dtype=np.uint8)
                    else:
                        output = np.zeros((len(images),) + edges.shape, dtype=np.uint8)
                else:
                    output = np.empty((len(images),) + edges.shape, dtype=np.uint8)

                #orig_output = np.empty((len(images),) + orig_edges.shape, dtype=np.uint8)
            
            #orig_output[i] = orig_edges
                
            if build_sparse:
                for j in np.where(edges==1):
                    output[i,j] = 1
            else:
                output[i] = edges

        ag.info("Running mixture model in BernoulliDetector")

        if output is None:
            raise Exception("Found no training images")

        if build_sparse:
            output = output.tocsr()
        elif sparse:
            output = scipy.sparse.csr_matrix(output)
        else:
            output = np.asmatrix(output)


        seed = self.settings.get('init_seed', 0)

        # Train mixture model OR SVM
    
        mixtures = []
        llhs = []
        for i in range(1):
            mixture = ag.stats.BernoulliMixture(self.num_mixtures, output, float_type=np.float32, init_seed=seed+i)
            minp = 0.01
            mixture.run_EM(1e-10, minp)
            mixtures.append(mixture)
            llhs.append(mixture.loglikelihood)

        best_i = np.argmax(llhs)
        mixture = mixtures[best_i]

        #mixture.templates = np.empty(0)

        # Now create our unspread kernels
        # Remix it - this iterable will produce each object and then throw it away,
        # so that we can remix without having to ever keep all mixing data in memory at once
        mixture.data_length -= extra_bits
        mixture.templates = mixture.templates[:,:-extra_bits]

        kernel_templates = np.clip(mixture.templates.reshape((self.num_mixtures,) + shape), 1e-5, 1-1e-5)
        kernel_sizes = [self.settings['image_size']] * self.num_mixtures

        #support = None
        if self.use_alpha:
            support = mixture.remix(alpha_maps).astype(np.float32) 
        else:
            support = None

        # Determine optimal bounding box for each component
        if 1:
            comps = mixture.mixture_components()
            self.determine_optimal_bounding_boxes(comps, alpha_maps)
    
        self.support = support
        #{{{ Old code
        if 0:
            kernel_templates = np.clip(mixture.remix_iterable(self.gen_img(images)), 1e-5, 1-1e-5)
            if 0:
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
        #}}}

        # Determine the log likelihood of the training data
        testing_type = self.settings.get('testing_type')
        fix_bkg = self.settings.get('fixed_bkg')
        if self.settings.get('bkg_type') == 'from-file':
            self.fixed_bkg = np.load(self.settings['fixed_bkg_file'])
            self.fixed_spread_bkg = np.load(self.settings['fixed_spread_bkg_file'])

        #fixed_bkg_file = self.settings.get('fixed_bkg_file')

        # TODO: This is all very broken
        self.prepare_eps(None)

        radii = self.settings['spread_radii']
        if testing_type == 'fixed':
            psize = self.settings['subsample_size']
            radii = self.settings['spread_radii']

            if 0:
                orig_output = None
                # Get images with the right amount of spreading
                for j, grayscale_img, img, alpha in self.load_img(images, offsets):
                    orig_edges = self.extract_spread_features(grayscale_img, settings=dict(spread_radii=radii))
                    if orig_output is None:
                        orig_output = np.empty((len(images),) + orig_edges.shape, dtype=np.uint8)
                    orig_output[j] = orig_edges
                        #orig_edges = self.extract_spread_features(grayscale_img)
                
            self.kernel_templates = kernel_templates

            #bkg = 1 - (1 - fix_bkg)**((2 * radii[0] + 1) * (2 * radii[1] + 1))
            unspread_bkg = self.bkg_model(None, spread=False)
            spread_bkg = self.bkg_model(None, spread=True)
            #bkg = 0.05

            # TODO: This gives a spread background!
            kernels = self.prepare_kernels(unspread_bkg, settings=dict(spread_radii=radii, subsample_size=psize))

            #sub_output = gv.sub.subsample(orig_output, psize, skip_first_axis=True)

            #import pylab as plt
            #plt.imshow(kernels[0].sum(axis=-1), interpolation='nearest')
            #plt.show()
            print((np.asarray(output).shape, shape))
            X = np.asarray(output).reshape((-1,) + shape) #sub_output.reshape((sub_output.shape[0], -1))
            llhs = [[] for i in range(self.num_mixtures)] 

            comps = mixture.mixture_components()
            for i, Xi in enumerate(X):
                mixcomp = comps[i]
                a = self.build_weights(kernels[mixcomp], spread_bkg)
                llh = np.sum(Xi * a)
                llhs[mixcomp].append(llh)

            self.standardization_info = [dict(mean=np.mean(llhs[k]), std=np.std(llhs[k])) for k in range(self.num_mixtures)]

        return mixture, kernel_templates, kernel_sizes, support

    def determine_optimal_bounding_boxes(self, comps, alpha_maps):
        self.extra['bbs'] = []
        for k in range(self.num_mixtures):
            ag.info("Determining bounding box for mixcomp", k)
            print('CP 1')
            alphas = alpha_maps[comps == k] 
            print('CP 2')
            bbs = list(map(gv.img.bounding_box, alphas)) 
            print('CP 3')

            #def score(bb):
                #return sum()
            #def loss(bb):
                #return -np.mean([gv.bb.fraction_metric(bb, bbi) for bbi in bbs])
            def loss(bb):
                return -np.mean([(gv.bb.fraction_metric(bb, bbi) > 0.5) for bbi in bbs]) - np.mean([gv.bb.fraction_metric(bb, bbi) for bbi in bbs]) * 0.01

            from scipy.optimize import minimize

            contendors = set() 
            # TODO: Up to 7 is not necessary here, but I guess it doesn't hurt either.
            for inflate in range(7):
                print(('CP 4', inflate))
                for bbi in bbs:
                    contendors.add(gv.bb.inflate(bbi, inflate)) 
            #import pdb; pdb.set_trace()

            contendors = list(contendors)

            print(('CP 5', len(contendors)))
            #best0 = np.argmin([loss(bbi) for bbi in contendors])
            from gv.fast import best_bounding_box

            contendors = np.asarray(contendors).astype(np.int64)
            bbs = np.asarray(bbs).astype(np.int64)

            best = best_bounding_box(contendors, bbs)
            print(('Best', best, best//len(bbs)))
            bb0 = contendors[best]
            print('CP 6')

            #{{{ Old code
            # Initialize with the first one
            if 0:
                res = minimize(loss, np.array(bb0))
                bb = tuple(res.x)

                # What is the worst value in this mixture component? If below 0.5, there might be no point keeping it
                print((k, 'loss', loss(bb)))
                print((k, 'minimum', min([gv.bb.fraction_metric(bb, bbi) for bbi in bbs])))
            #}}}
            bb = bb0

            # Now, inflate the bounding box
            #inf_bb = self.settings.get('inflate_bounding_box')
            #if inf_bb is not None:
                #bb = gv.bb.inflate(bb, inf_bb)

            #psize = self.settings['subsample_size']
            #bb = tuple([(bb[i] - alphas.shape[i%2] // 2) / psize[i%2] for i in xrange(4)])

            self.extra['bbs'].append(bb)
        ag.info('Done determining all bounding boxes')

    def extract_unspread_features(self, image):
        edges = self.descriptor.extract_features(image, dict(spread_radii=(0, 0), crop_border=self.settings.get('crop_border')))
        return edges

    def extract_spread_features(self, image, must_preserve_size=False):
        edges = self.descriptor.extract_features(image, dict(spread_radii=self.settings['spread_radii'], 
                                                             subsample_size=self.settings['subsample_size'], 
                                                             crop_border=self.settings.get('crop_border'),
                                                             rotation_spreading_radius=self.settings.get('rotation_spreading_radius', 0)),
                                                 must_preserve_size=must_preserve_size)
        return edges 

    @property
    def unpooled_kernel_size(self):
        return self.kernel_templates[0].shape[:2]

    @property
    def unpooled_kernel_side(self):
        return max(self.unpooled_kernel_size)

    def bkg_model(self, edges, spread=False, location=None):
        """
        Returns unspread background model in three different ways:

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

        elif bkg_type == 'corner':
            assert not self.settings.get('train_unspread')
            if spread:
                return self.kernel_templates[0][0,0]
            else:
                return None

        elif bkg_type == 'from-file':
            if spread:
                return self.fixed_spread_bkg
            else:
                return self.fixed_bkg

        elif bkg_type == 'per-image-average':
            bkg = edges.reshape((-1, self.num_features)).mean(axis=0)
            # TODO: min_probability is too high here
            eps = 1e-10
            bkg = np.clip(bkg, eps, 1 - eps)
            return bkg

        elif bkg_type == 'smoothed':
            pass

        else:
            raise ValueError("Specified background model not available")

    #def subsample(self, edges):
        #return gv.sub.subsample(edges, self.settings['subsample_size'])

    #{{{ Old code
    if 0:
        def prepare_mixcomp_kernels(self, mixcomp, unspread_bkg, settings={}):
            sett = self.settings.copy()
            sett.update(settings) 

            if sett.get('kernel_ready'):
                return self.kernel_templates[mixcomp]
            
            assert 0, "Use prepare_kernels"
    #}}}

    def prepare_kernels(self, unspread_bkg, settings={}):
        sett = self.settings.copy()
        sett.update(settings) 

        if sett.get('kernel_ready'):
            return self.kernel_templates 

        if not self.use_basis:
            kernels = deepcopy(self.kernel_templates)

        psize = sett['subsample_size']

        if self.train_unspread:
            # TODO: This does not handle irregular-sized kernel_templates objects!

            radii = sett['spread_radii']
            #neighborhood_area = ((2*radii[0]+1)*(2*radii[1]+1))

            if self.use_basis:
                #global cad_kernels
                a = 1 - unspread_bkg.sum()
                bkg_categorical = np.concatenate(([a], unspread_bkg))

                C = self.kernel_basis * np.expand_dims(bkg_categorical, -1)
                kernels = C.sum(axis=-2) / self.kernel_basis_samples.reshape((-1,) + (1,)*(C.ndim-2))

                kernels = np.clip(kernels, 1e-5, 1-1e-5)

            #unspread_bkg = 1 - (1 - bkg)**(1/neighborhood_area)
            #unspread_bkg = 1 - (1 - bkg)**50
            unspread_bkg = np.clip(unspread_bkg, 1e-5, 1-1e-5)
        
            aa_log = [ag.util.multipad(np.log(1 - kernel), (radii[0], radii[1], 0), np.log(1-unspread_bkg)) for kernel in kernels]

            integral_aa_log = [aa_log_i.cumsum(1).cumsum(2) for aa_log_i in aa_log]

            offsets = gv.sub.subsample_offset(kernels[0], psize)

            # Fix kernels
            istep = 2*radii[0]
            jstep = 2*radii[1]
            for mixcomp in range(self.num_mixtures):
                sh = kernels[mixcomp].shape[:2]
                # Note, we are going in strides of psize, given a certain offset, since
                # we will be subsampling anyway, so we don't need to do the rest.
                for i in range(offsets[0], sh[0], psize[0]):
                    for j in range(offsets[1], sh[1], psize[1]):
                        p = gv.img.integrate(integral_aa_log[mixcomp], i, j, i+istep, j+jstep)
                        kernels[mixcomp][i,j] = 1 - np.exp(p)

            

            # Subsample kernels
            sub_kernels = [gv.sub.subsample(kernel, psize) for kernel in kernels]
        else:
            sub_kernels = kernels

            if self.use_basis:
                a = 1 - unspread_bkg.sum()

                C = self.kernel_basis * np.expand_dims(unspread_bkg, -1)
                kernels = a * cad_kernels + C.sum(axis=-2) / self.kernel_basis_samples
                

        for i in range(self.num_mixtures):
            sub_kernels[i] = np.clip(sub_kernels[i], self.eps, 1-self.eps)

        K = self.settings.get('quantize_bins')
        if K is not None:
            assert 0, "Does not work with different size kernels"
            sub_kernels = np.round(1 + sub_kernels * (K - 2)) / K


        return sub_kernels

    def detect_coarse_single_factor(self, img, factor, mixcomp, 
                                    img_id=0, use_padding=True, 
                                    use_scale_prior=True, cascade=True,
                                    more_detections=False,
                                    farming=False,
                                    discard_weak=False,
                                    return_bounding_boxes=True,
                                    must_preserve_size=False,
                                    strides=(1, 1),
                                    save_samples=False):
        """
        TODO: Experimental changes under way!
        """

        bb_bigger = (0, 0, img.shape[0], img.shape[1])
        img_resized = gv.img.resize_with_factor_new(gv.img.asgray(img), 1/factor) 

        last_resmap = None

        psize = self.settings['subsample_size']
        radii = self.settings['spread_radii']
        rotspread = self.settings.get('rotation_spreading_radius', 0)
        cb = self.settings.get('crop_border')

        #spread_feats = self.extract_spread_features(img_resized)
        spread_feats = self.descriptor.extract_features(img_resized, dict(spread_radii=radii, subsample_size=psize, rotation_spreading_radius=rotspread, crop_border=cb, adapt=True), must_preserve_size=must_preserve_size)

        #unspread_feats = self.descriptor.extract_features(img_resized, dict(spread_radii=(0, 0), subsample_size=psize, crop_border=cb))

        # TODO: Avoid the edge for the background model
        spread_bkg = self.bkg_model(spread_feats, spread=True)
        #unspread_bkg = self.bkg_model(unspread_feats, spread=False)
        #unspread_bkg = np.load('bkg.npy')
        #spread_bkg = 1 - (1 - unspread_bkg)**25
        #spread_bkg = np.load('spread_bkg.npy')

        unspread_bkg = None

        #feats = gv.sub.subsample(spread_feats, psize) 
        sub_kernels = self.prepare_kernels(unspread_bkg, settings=dict(spread_radii=radii, subsample_size=psize))
        bbs, resmap, bkgcomp = self._detect_coarse_at_factor(spread_feats, 
                                                             sub_kernels, 
                                                             spread_bkg, 
                                                             factor, 
                                                             mixcomp, 
                                                             bb_bigger,
                                                             image=img_resized, 
                                                             img_id=img_id,
                                                             use_padding=use_padding, 
                                                             use_scale_prior=use_scale_prior,
                                                             cascade=cascade,
                                                             more_detections=more_detections,
                                                             farming=farming,
                                                             discard_weak=discard_weak,
                                                             return_bounding_boxes=return_bounding_boxes,
                                                             strides=strides,
                                                             save_samples=save_samples)

        final_bbs = bbs

        return final_bbs, resmap, bkgcomp, spread_feats, img_resized

    #{{{ calc_score - not used!
    def calc_score(self, img, factor, bbobj, score=0):
        llhs = score
    
        i0, j0 = bbobj.score0, bbobj.score1

        # TODO: Temporary
        img_resized = gv.img.resize_with_factor_new(img, factor)
        factor = 1.
        mixcomp = 0

        psize = self.settings['subsample_size']
        radii = self.settings['spread_radii']

        feats = self.extract_spread_features(img_resized)

        # Last psize
        d0, d1 = (14, 44) 

        pad = 50 

        unspread_feats = extract2(img_resized)
        #unspread_feats_pad = ag.util.zeropad(unspread_feats, (pad, pad, 0))
        #unspread_feats0 = unspread_feats_pad[-10 + pad+i0-d0//2:10+ pad+i0-d0//2+d0, -10 + pad+j0-d1//2:10 + pad+j0-d1//2+d1]

        #bkg = self.bkg_model(unspread_feats0)
        unspread_bkg = self.bkg_model(unspread_feats, spread=False)

        #feats = gv.sub.subsample(up_feats, psize) 
        feats_pad = ag.util.zeropad(feats, (pad, pad, 0))
        feats0 = feats_pad[pad+i0-d0//2:pad+i0-d0//2+d0, pad+j0-d1//2:pad+j0-d1//2+d1]
        #{{{ Old code
        if 0:
            sub_kernels = self.prepare_kernels(unspread_bkg, settings=dict(spread_radii=radii, subsample_size=psize))

            neighborhood_area = ((2*radii[0]+1)*(2*radii[1]+1))
            # TODO: Don't do this anymore
            spread_back = 1 - (1 - unspread_bkg)**neighborhood_area
            self.eps = self.settings['min_probability']
            spread_back = np.clip(spread_back, self.eps, 1 - self.eps)

            weights = self.build_weights(sub_kernels[0], spread_back)
            weights_plus = np.clip(np.log(sub_kernels[0]/(1-sub_kernels[0]) * ((1-spread_back)/spread_back)), 0, np.inf)


            llhs = (feats0 * weights + feats0 * weights_plus * 4).sum()
        #}}}

        #means = feats0.reshape((-1, self.num_features)).mean(axis=0)
        if feats0.mean() < 0.02:
            return 0 
        else:
            return llhs
    #}}}

    def determine_scores(self, img, fileobj=None, mixcomps=None, one_centered=False):
        """
        This function is different from detect, since it does not try to arrange
        an appropriate bounding box for each detection. Instead, it goes through
        all windows exhausitvely and reports detection score. Instead of returning
        DetectionBB objects, it simply returns a list of all scores.

        The images are all assumed to be negatives, so no labels are returns either.
        """
        assert self.num_mixtures == 1, 'Only works with one mixcomp for now'
        if mixcomps is None:
            mixcomps = [0]

        classify_stride = self.settings.get('classify_stride', 8)
        subsample_size = self.descriptor.subsample_size
        assert classify_stride % subsample_size[0] == 0 and classify_stride % subsample_size[1] == 0, 'subsample_size must divide classify_stride'
        strides = (classify_stride // subsample_size[0], classify_stride // subsample_size[1])

        if one_centered:
            img = gv.img.crop(img, self.settings['image_size']) 
            bbs0, resmap, bkgcomp, feats, img_resized = \
                    self.detect_coarse_single_factor(img, 
                                                     1.0, 
                                                     mixcomps[0], 
                                                     img_id=None, 
                                                     use_padding=False, 
                                                     use_scale_prior=False,
                                                     return_bounding_boxes=False,
                                                     strides=strides)

            th = self.settings.get('classify_threshold', -np.inf)
            scores = resmap.ravel()
            windows_count = scores.size
            scores = scores[scores > th]


        else:
            scores, windows_count = self.detect_coarse(img, fileobj=fileobj, mixcomps=mixcomps, use_scale_prior=False, 
                                                       #must_preserve_size=True,
                                                       return_scores_only=True, strides=strides)
            # Calculate windows count exactly
            img_size = img.shape[:2]
            count = 0
            while True:
                add = (max(0,int(img_size[0]-128+8)) // 8) * (max(0,int(img_size[1]-64+8)) // 8)
                if add == 0:
                    break
                count += add
                img_size = (img_size[0]//1.2, img_size[1]//1.2)

            # Use this instead, to give a consistent total count. The actual negatives could
            # perhaps vary a bit however.
            windows_count = count

        return scores, windows_count


    def detect(self, img, fileobj=None, mixcomps=None, use_scale_prior=True):
        bbs = self.detect_coarse(img, fileobj=fileobj, mixcomps=mixcomps, use_scale_prior=use_scale_prior) 

        # This is just to cut down on memory load because of the detections

        bbs = bbs[:20]

        return bbs

    #{{{ Old function
    def detect_coarse_OLD(self, img, fileobj=None, mixcomps=None):
        if mixcomps is None:
            mixcomps = list(range(self.num_mixtures))

        # TODO: Temporary stuff
        if 0:
            #{{{
            bbs = []
            for mixcomp in mixcomps:
                bbs0, resmap, bkgcomp, feats, img_resized = self.detect_coarse_single_factor(img, 1.0, mixcomp, img_id=fileobj.img_id)
                bbs += bbs0

            # Do NMS here
            final_bbs = self.nonmaximal_suppression(bbs)
            
            # Mark corrects here
            if fileobj is not None:
                self.label_corrects(final_bbs, fileobj)


            return final_bbs
            #}}}
        else:
            # TODO: This does not use a Guassian pyramid, so it
            # resizes everything from scratch, which is MUCH SLOWER

            min_size = self.settings['min_size'] 
            min_factor = min_size / max(self.orig_kernel_size)
            max_size = self.settings['max_size'] 
            max_factor = max_size / max(self.orig_kernel_size)

            num_levels = 2
            factors = []
            skips = 0
            eps = 1e-8
            for i in range(1000):
                factor = self.settings['scale_factor']**(i-1)
                if factor > max_factor+eps:
                    break
                if factor >= min_factor-eps:
                    factors.append(factor) 
                else:
                    skips += 1
            num_levels = len(factors) + skips

            if 1:
                bbs = []
                for i, factor in enumerate(factors):
                    for mixcomp in mixcomps:
                        bbs0, resmap, bkgcomp, feats, img_resized = self.detect_coarse_single_factor(img, factor, mixcomp, img_id=fileobj.img_id)
                        bbs += bbs0
            else:
                # {{{
                bbs = []
                for mixcomp in mixcomps:
                    bbsi = []
                    for i, factor in enumerate(factors):
                        bbs0, resmap, bkgcomp, feats, img_resized = self.detect_coarse_single_factor(img, factor, mixcomp, img_id=fileobj.img_id)

                        bbsi += bbs0

                    bbsi = self.nonmaximal_suppression(bbsi)
                    if fileobj is not None:
                        self.label_corrects(bbsi, fileobj)
    
                    bbs += bbsi
                #}}}
        
            if 1:
                # Do NMS here
                final_bbs = self.nonmaximal_suppression(bbs)

                # Mark corrects here
                if fileobj is not None:
                    self.label_corrects(final_bbs, fileobj)

            else:
                final_bbs = bbs


            return final_bbs

        # ********************** OLD STUFF *****************************

        # Build image pyramid
        min_size = self.settings['min_size'] 
        min_factor = min_size / max(self.orig_kernel_size)#self.unpooled_kernel_side

        max_size = self.settings['max_size'] 
        max_factor = max_size / max(self.orig_kernel_size)#self.unpooled_kernel_side

        num_levels = 2
        factors = []
        skips = 0
        eps = 1e-8
        for i in range(1000):
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
        def extract2(image):
            return self.descriptor.extract_features(image, dict(spread_radii=(0, 0), preserve_size=False))
        def extract(image):
            return self.descriptor.extract_features(image, dict(spread_radii=self.settings['spread_radii'], preserve_size=True))

        edge_pyramid = list(map(self.extract_spread_features, pyramid))
        ag.info("Getting edge pyramid")
        unspread_edge_pyramid = list(map(extract, pyramid))
        spread_edge_pyramid = list(map(extract, pyramid))

        ag.info("Extract background model")
        unspread_bkg_pyramid = list(map(self.bkg_model, unspread_edge_pyramid))
        spread_bkg_pyramid = [self.bkg_model(p, spread=True) for p in spread_edge_pyramid]

        ag.info("Subsample")
        #small_pyramid = map(self.subsample, edge_pyramid) 

        bbs = []
        for i, factor in enumerate(factors):
            # Prepare the kernel for this mixture component
            ag.info("Prepare kernel", i, "factor", factor)
            sub_kernels = self.prepare_kernels(unspread_bkg_pyramid[i][0])

            for mixcomp in mixcomps:
                ag.info("Detect for mixture component", mixcomp)
            #for mixcomp in [1]:
                bbsthis, _, _ = self._detect_coarse_at_factor(edge_pyramid[i], sub_kernels, spread_bkg_pyramid[i][1], factor, mixcomp)
                bbs += bbsthis

        ag.info("Maximal suppression")
        # Do NMS here
        final_bbs = self.nonmaximal_suppression(bbs)
        
        # Mark corrects here
        if fileobj is not None:
            self.label_corrects(final_bbs, fileobj)

        return final_bbs
    #}}}

    def detect_coarse(self, img, fileobj=None, mixcomps=None, return_resmaps=False, use_padding=True, use_scale_prior=True, cascade=True, more_detections=False, farming=False, discard_weak=False, return_scores_only=False, strides=(1, 1), save_samples=False):
        if mixcomps is None:
            mixcomps = list(range(self.num_mixtures))

        prepare_resmaps = return_resmaps or return_scores_only

        # TODO: Temporary stuff
        # TODO: This does not use a Guassian pyramid, so it
        # resizes everything from scratch, which is MUCH SLOWER

        min_size = self.settings['min_size'] 
        img_size = max(self.settings['image_size'])
        min_factor = min_size / img_size 
        max_size = self.settings['max_size'] 
        max_factor = max_size / img_size 

        resmaps = {}

        num_levels = 2
        factors = []
        skips = 0
        eps = 1e-8
        for i in range(1000):
            factor = self.settings['scale_factor']**(i-1)
            if factor > max_factor+eps:
                break
            if factor >= min_factor-eps:
                factors.append(factor) 
            else:
                skips += 1
        num_levels = len(factors) + skips

        # Number of windows processed
        windows_count = 0
        bbs = []
        for i, factor in enumerate(factors):
            resmaps_factor = []
            for mixcomp in mixcomps:
                img_id = fileobj.img_id if fileobj is not None else -1
                bbs0, resmap, bkgcomp, feats, img_resized = \
                        self.detect_coarse_single_factor(img, 
                                                         factor, 
                                                         mixcomp, 
                                                         img_id=img_id, 
                                                         use_padding=use_padding, 
                                                         use_scale_prior=use_scale_prior,
                                                         cascade=cascade,
                                                         more_detections=more_detections,
                                                         farming=farming,
                                                         discard_weak=discard_weak,
                                                         must_preserve_size=True,
                                                         return_bounding_boxes=not return_scores_only,
                                                         strides=strides,
                                                         save_samples=save_samples)
                bbs += bbs0
                windows_count += np.prod(resmap.shape)
                if prepare_resmaps:
                    resmaps_factor.append(resmap)

            if prepare_resmaps:
                resmaps[factor] = resmaps_factor

    
        # Do NMS here
        final_bbs = self.nonmaximal_suppression(bbs)

        # Mark corrects here
        if fileobj is not None:
            self.label_corrects(final_bbs, fileobj)

        ret = ()
        if return_scores_only:
            assert len(mixcomps) == 1, 'return_scores_only only works with 1 mixcomp for now'
            th = self.settings.get('classify_threshold', -np.inf)
            scores = np.concatenate([resmap[0][resmap[0] > th].ravel() for resmap in list(resmaps.values())]) 
            ret += (scores, windows_count)
        else:
            ret += (final_bbs,)

        if return_resmaps:
            ret += (resmaps,)
        
        if len(ret) == 1:
            return ret[0]
        else:
            return ret

    def param(self, default):
        if self._param is None:
            return default
        else:
            return self._param

    def _detect_coarse_at_factor(self, 
                                 sub_feats, 
                                 sub_kernels, 
                                 spread_bkg, 
                                 factor, 
                                 mixcomp, 
                                 bb_bigger,
                                 image=None, 
                                 img_id=None,
                                 use_scale_prior=True, 
                                 use_padding=True,
                                 cascade=True,
                                 more_detections=False,
                                 farming=False,
                                 discard_weak=False,
                                 return_bounding_boxes=True,
                                 strides=(1, 1),
                                 save_samples=False):

        if 0:
            self.standardization_info[mixcomp]['mean'] = self.extra['bkg_mixtures'][mixcomp]['mean']
            self.standardization_info[mixcomp]['std'] = self.extra['bkg_mixtures'][mixcomp]['std']

            self.indices[mixcomp] = self.extra['bkg_mixtures'][mixcomp]['indices']

            sub_kernels = [[self.extra['bkg_mixtures'][i][j]['kern'] for j in range(1)] for i in range(self.num_mixtures)]
            spread_bkg = [[self.extra['bkg_mixtures'][i][j]['bkg'] for j in range(1)] for i in range(self.num_mixtures)]

        resmap, bigger, weights, padding = self.response_map(sub_feats, sub_kernels, spread_bkg, mixcomp, level=-1, use_padding=use_padding, strides=strides)

        orig_resmap = resmap.copy()

        if use_scale_prior and not farming:
            resmap += self.settings.get('scale_prior', 0.0) * factor

        #{{{ Old experiments
        elif 0:

            #resmap -= #
            if 0: # Attempt 1
                data = np.load('tt.npz')
                xs, tt = data['xs'], data['tt']
                pos_mu = 7.0
                i = np.argmin(np.fabs((2**xs - 100*factor*0.6)**2))
                resmap -= np.log(tt[i]) / pos_mu

            elif 1: # Attempt 2
                import scipy.stats as st
                #data = np.load('tt.npz')
                #xs, tt = data['xs'], data['tt']
                # Attempt 4
                #tt *= 1 # 15.06
                #tt /= 3 # 15.24
                #mus = st.norm.ppf(1 - 1/tt)
                #sigs = st.norm.ppf(1 - 1/tt/np.exp(1)) - mus
                # TODO: Figure out factor index better, or don't use factor index at all and match it better
                i = int(np.round(np.log2(factor) / np.log2(self.settings['scale_factor'])))
                f = np.log2(100*factor*0.8)

                #pos_mu = self.standardization_info[mixcomp]['pos_llhs'].mean()
                #i = np.argmin(np.fabs((xs - f)**2))
                #resmap -= np.log(tt[i]) / pos_mu
                #alpha = 1/(1 + tt[i])

                # 17.09%
                factor_info = self.standardization_info[mixcomp]['factor_info'][i]
                data = factor_info['neg_llhs']
                nonzero = data != 0.0
                alpha = nonzero.mean()**3
                data = data[nonzero]

                resmap -= data.mean() 
                resmap /= data.std() 
                #resmap -= factor_info['neg_mean'] 
                #resmap /= factor_info['neg_std'] 
                resmap += 2 * f * np.log(2) - np.log(alpha)
        #}}} 
        #{{{ Old code

                if 0: # 11.94 (limit500)
                    pos_mu = 0.5
                    if 0:
                        pos_mu = 3.0 # 11.57
                        pos_mu = 1.0 # 14.03
                        pos_mu = 0.5 # 15.64
                        pos_mu = 0.25 # 14.68
                        pos_mu = 0.75 # 14.56

                        pos_mu = 0.5
                    #resmap *= pos_mu
                    #resmap -= pos_mu**2 / 2
                    #resmap += np.log(alpha / (1 - alpha))
                    #means = np.load('means{}.npy'.format(mixcomp))
                    #resmap += -np.log(tt[i]) / pos_mu - means[np.round(np.log2(factor)*3)] / self.standardization_info[mixcomp]['neg_llhs'].std()
                    #resmap += (2 * f * np.log(2) - means[np.round(np.log2(factor)*3)] / self.standardization_info[mixcomp]['neg_llhs'].std()) / 1


                #resmap -= mus[i]
                if 0: # Attempt 3
                    resmap /= sigs[i]

        #ss = np.log2(0.5 * factor * 100)
        #resmap += 2 * ss
        #aa = 0.25
        #from scipy import stats as st
        #resmap += (np.log(2**(2*ss)) + st.norm.logpdf(ss, loc=7.5, scale=1.6)) * aa 
        
        # TODO: Temporary experiment
        #resmap -= 9 * np.exp(1 - factor) 
        #resmap -= 12 - 3 * factor
        #resmap += 0.75 * factor * (1 - aa)

        #ff = factor * 0.5
        #ff = np.clip(ff, 0, 4)
        #resmap += 1.75 * np.log(ff) / np.log(2)
        #resmap += 2.3 * np.log(ff)

        #data = np.load('ss.npz')
        #x0, y0 = data['x0'], data['y0']

        if 0:
            data = np.load('ss.npz')

            x0, y0 = data['x0'], data['y0']
            x0 = x0[120:]
            y0 = y0[120:]

        if 0:
            pixels0 = 1000000 / (2**x0)**2 
            amount = pixels0 * 10

            mus = np.asarray([st.norm.ppf(1 - 1/Ns[i]) for i in range(len(amount))])
            sigs = np.asarray([st.norm.ppf(1 - 1/Ns[i]/np.exp(1)) - mus[i] for i in range(len(amount))])

            def rescale(x, i):
                #mu = st.norm.ppf(1 - 1/amount[i])
                #sig = st.norm.ppf(1 - 1/amount[i]/np.exp(1)) - mu
                mu = mus[i]
                sig = sigs[i]

                mu0 = mu + sig * EM
                sig0 = sig * np.pi / np.sqrt(6)

                #Ireturn x + means[i]
                #return (x - mu) / sig
                #return (x - mu0) / sig0 
                xc = max(x, mu-0.5)
                v = st.genextreme.logcdf(xc, 0, loc=mu, scale=sig)
                if np.isinf(v):
                    raise Exception('Something bad happened') 
                return v

        #f = np.log(100 * factor * 0.8) / np.log(2)
        #ii = np.fabs(f - x0).argmin()

        if 0:
            from gv.fast import nonparametric_rescore
            data = np.load('st2.npz')
            start, step, points = data['start'], data['step'], data['points']
            nonparametric_rescore(resmap, start, step, points[ii])

        #z0 = np.log(y0 * x0**2)

        #resmap += z0[np.fabs(x0 - f).argmin()]
        
        #if factor < 3:
        #    resmap += np.log(0.1 * factor)
        #else:
        #    resmap += np.log(0.3)
        ##resmap += np.log(

        #}}}

        bkgcomp = np.zeros_like(resmap)

        if resmap.size == 0:
            return [], resmap, bkgcomp

        #kern = sub_kernels[mixcomp]

        psize = self.settings['subsample_size']

        # TODO: Decide this in a way common to response_map
        sh = self.weights_shape(mixcomp)
        sh0 = sh

        #sh = kern.shape
        #sh0 = kern.shape

        image_padding = (padding[0] * psize[0], padding[1] * psize[1])

        # Get size of original kernel (but downsampled)
        full_sh = self.kernel_sizes[mixcomp]
        sh2 = sh
        sh = (full_sh[0]//psize[0], full_sh[1]//psize[1])

        from scipy.stats import scoreatpercentile

        th = scoreatpercentile(resmap.ravel(), 50)
        #th = -0.1
        #th = -np.inf
    
        #th = resmap.mean() 
        bbs = []

        agg_factors = tuple([psize[i] * factor for i in range(2)])
        agg_factors2 = tuple([factor for i in range(2)])
        #bb_bigger = (0.0, 0.0, sub_feats.shape[0] * agg_factors[0], sub_feats.shape[1] * agg_factors[1])

        # TODO: New
        if self.TEMP_second:
            kern0 = np.clip(kern[0], self.eps, 1 - self.eps)
            #bkg2 = np.clip(self.fixed_spread_bkg2[mixcomp][0], eps, 1 - eps)
            #weights2 = np.log(kern0 / (1 - kern0) * ((1 - bkg2) / bkg2))

        def phi(X, mixcomp, use_indices):
            #if use_indices: 
            if self.indices2 is not None and 0:
                indices = self.indices2[mixcomp]
                return X.ravel()[np.ravel_multi_index(indices.T, X.shape)]
            else:
                #return gv.sub.subsample(X, (2, 2)).ravel()
                return X.ravel()
            #return X.ravel()

        # TODO: Remove
        #C = np.load('cov.npy')
        #invC = np.load('invcov.npy')

        
        fs = []

        if 0:
            # TODO: Temporary stuff
            feats = np.load('uiuc-feats.npy')
            frames = np.apply_over_axes(np.mean, feats, [1, 2]).squeeze() 
            means = frames.mean(0)
            stds = frames.std(0)


        # TODO: Temporary stuff
        if 0:
            G_Sigma = self.extra['sturf'][mixcomp]['G_Sigma']
            G_mu1 = self.extra['sturf'][mixcomp]['G_mu']
            G_mu0 = np.log(self.fixed_spread_bkg[0].mean(0).mean(0))
            B = np.linalg.solve(G_Sigma, G_mu1 - G_mu0)

        # Don't bother with this if we're not returning bounding boxes
        if return_bounding_boxes:
            #import scipy.signal 
            #local_maxes = scipy.signal.convolve2d(resmap, np.ones((5, 5))
            
            # TODO: This could be too big for a lot of subsampling
            #if more_detections:
            #    s = 3
            ##else:
            #    s = 5 
            s = self.settings.get('scan_step_size', 3)
            find_best_in_scan_window = self.settings.get('find_best_in_scan_window', True)  

            # Scan radius for when cascading
            r = 3

            FULL = False 
            if FULL:
                s = 1
                th = -np.inf


            # TEMP [
            if not self.settings.get('plain') and False: # TODO
                F = self.num_features
                w = self.extra['weights'][mixcomp]
                II = self.indices[mixcomp]
                w_kp = w[tuple(II.T)].reshape((-1, F))
                Ls = np.bincount(II[:,2], minlength=F)
                avgL = np.mean(Ls)

                L = np.prod(w.shape[:2])
                #Xsum = np.apply_over_axes(np.sum, X, [0, 1])
                sturf = self.extra['sturf'][mixcomp]
                if 0:
                    #Zs = sturf['Zs'][:50]
                    Zs = sturf['actual_Zs_neg'][:200]
                    #Zs_pos = sturf['Zs_pos10'][:50]
                    #Zs_pos = sturf['actual_Zs'][:100] 
                    Zs_pos = Zs
                    rs = np.random.RandomState(0)
                    #term0_neg = np.apply_over_axes(np.sum, np.log(1 - gv.sigmoid(w_kp[np.newaxis] + gv.logit(Zs[:,np.newaxis]))), [1, 2]).squeeze()
                    term0_pos = np.apply_over_axes(np.sum, np.log(1 - gv.sigmoid(w_kp[np.newaxis] + gv.logit(Zs_pos[:,np.newaxis]))), [1, 2]).squeeze()
                    term0_neg = avgL * np.log(1 - Zs).sum(1)

            mn, mx = np.inf, -np.inf

            # ]

            for i0 in range(0, resmap.shape[0], s):
                for j0 in range(0, resmap.shape[1], s):
            #resmap[:] = -1000 
            #for i0 in [19, 30, 5, 30, 25, 10, 14]:
                #for j0 in [23, 5, 14, 10, 20, 40, 30]:
                    win = resmap[i0:i0+s, j0:j0+s]
                    #local_max = resmap[max(0, i-3):i+4, max(0, j-3):j+4].argmax()
                    if find_best_in_scan_window:
                        di, dj = np.unravel_index(win.argmax(), win.shape) 
                        i = i0 + di
                        j = j0 + dj
                    else:
                        i = i0
                        j = j0
    
                    score = resmap[i,j]
                    orig_score = orig_resmap[i,j]
                    X = None
                    if score < th:
                        continue
                    else:
                    #if score >= th:# and score == local_max:

                        # ...
                        #if self.TEMP_second and self.clfs is not None:
                            #th = self.clfs[mixcomp]['th']

                        cascade_score = self.extra.get('cascade_threshold')
                        do_cascade = self.settings.get('cascade')
                        bk = -1 
                        X = bigger[i:i+sh0[0], j:j+sh0[1]].copy()

                        # TODO: Rel model attempts
                        if 0:
                            sturf = self.extra['sturf'][mixcomp]
                            # Remove prior stuff 
                            par = self.param(1.0)


                            support0 = sturf['support'][...,np.newaxis]
                            avgf = np.apply_over_axes(np.sum, X * support0, [0, 1]) / support0.sum()
                            avgf = gv.bclip(avgf, 0.001).squeeze()

                            xx = logit(avgf)


                            def quad_inv(A, x):
                                return np.dot(x, np.linalg.solve(A, x))

                            cons = self.extra['concentrations'][mixcomp]
                            pos_cov = cons['logit_pos_cov'].copy()
                            neg_cov = cons['logit_neg_cov'].copy()
                            pos_avg = cons['logit_pos_avg']
                            neg_avg = cons['logit_neg_avg']

                            F = self.num_features


                            r = 1.5 
                            # Regularize stuff
                            pos_cov += np.eye(F) * 10**r 
                            neg_cov += np.eye(F) * 10**r 

                            pos_sol = quad_inv(pos_cov, xx - pos_avg)
                            neg_sol = quad_inv(neg_cov, xx - neg_avg)

                            prior = 0.5 * (neg_sol - pos_sol)
                            #prior = -pos_sol

                            prior *= par

                            new_score = score + prior
                            score = 100000000 + new_score

                            resmap[i,j] = new_score#.clip(min=0) #new_score 

                        elif 1 and cascade and 'sturf' in self.extra and not self.settings.get('plain'):
                            sturf = self.extra['sturf'][mixcomp]
                            support0 = sturf['support'][...,np.newaxis]
                            avgf = np.apply_over_axes(np.sum, X * support0, [0, 1]) / support0.sum()
                            avgf = gv.bclip(avgf, 0.025)[0]
                            w = self.new_kp_weights(mixcomp) 

                            #V = (avgf * (1 - avgf) * w**2).sum()

                            wsum = w.sum(0)
                            C = sturf['Sneg']
                            V = np.dot(wsum, np.dot(C, wsum))

                            #score = w * avgf

                            X_kp = X[tuple(self.indices[mixcomp].T)].reshape(w.shape[:2])
                            assert np.fabs(score - (X_kp * w).sum()) < 1e-5


                            new_score = (score - (w * avgf).sum()) / np.sqrt(V)
                            score = 100000 + new_score 
                            resmap[i,j] = new_score 


                        if 0 and cascade and 'sturf' in self.extra:
                            sturf = self.extra['sturf'][mixcomp]
                            #rew = sturf['reweighted']
                            kp = self.keypoint_mask(mixcomp)
                            support0 = sturf['support'][...,np.newaxis]
                            #support = sturf['support'][...,np.newaxis] * kp
                            #avg = np.apply_over_axes(np.sum, X * support, [0, 1]) / np.apply_over_axes(np.sum, support, [0, 1])
                            avgf = np.apply_over_axes(np.sum, X * support0, [0, 1]) / support0.sum()

                            #avg_rew = np.apply_over_axes(np.mean, rew, [0, 1])
                            #lmb = avg_rew * support.sum()

                            #beta = sturf['wavg'] * np.apply_over_axes(np.sum, support, [0, 1])
                            #betaf = sturf['wavg'] * support0.sum()

                            X_kp = X[tuple(II.T)].reshape((-1, F))

                            pavg = sturf['pavg']
                            #S = sturf['S']

                            F = self.num_features
                            navg = sturf['navg']
                            par = self.param(1.0)
                            #Spos = par * sturf['S']# + np.eye(F) * 0.001
                            #Sneg = 1.0 * sturf['Sneg']# + np.eye(F) * 0.001

                            

                            #old_score = np.sum(X * w * kp)

                            # new score
                            #alpha = 0.0

                            #bkg = np.load('uiuc-bkg.npy')
                            #diff = avg - sturf['bkg']
                            #diff = avg - bkg
                            

                            #factor = support.sum() / support0.sum()

                            #score1 = old_score - np.sum(avg * beta)
                            #score2 = old_score - np.sum(avgf * beta)

                            #obj_avg = np.apply_over_axes(np.sum, (self.kernel_templates[0] * support0), [0, 1]) / support0.sum()

                            #d = (avgf * beta).ravel()
                            d = (avgf - pavg).ravel()

                            bb = gv.bclip(avgf.ravel(), 0.01)

                            def clogit(x):
                                return gv.logit(gv.bclip(x, 0.001))

                            if 1:
                                #Eprior = norm.logpdf(
                                #Eprior = 
                                from scipy.misc import logsumexp
                                try:
                                    from scipy.stats import multivariate_normal 
                                except ImportError:
                                    # File copied from scipy 0.14
                                    from mvn import multivariate_normal
                                #top = logsumexp(np.sum(w * X) + np.sum(X * gv.logit(Zs)
                                #bot = 

                                #with gv.Timer('stand'):
                                #term1 = (X[np.newaxis] * gv.logit(Zs[:,np.newaxis,np.newaxis])).sum(1).sum(1).sum(1)
                                #SI = self.standardization_info[mixcomp]
                                #unstand_score = (X * w).sum()# * SI['std'] + SI['mean']
                                unstand_score = (X_kp * w_kp).sum()

                                #Zs = Zs.clip(min=0.025)

                                #H = np.log(1 - gv.sigmoid(w[np.newaxis] + gv.logit(Zs[:,np.newaxis,np.newaxis])))

                                if 0:
                                    #L = np.prod(X.shape[:2])
                                    Xsum = np.apply_over_axes(np.sum, X_kp, [0])
                                    term1_neg = (Xsum * gv.logit(Zs)).sum(1)
                                    term1_pos = (Xsum * gv.logit(Zs_pos)).sum(1)

                                    PP = logsumexp(
                                            term0_pos + 
                                            term1_pos
                                            )

                                    NN = logsumexp(
                                            term0_neg + 
                                            term1_neg
                                            )

                                else:
                                    PP = NN = 0

                                score = 100000 + unstand_score + PP - NN

                                mn = min(mn, PP - NN)
                                mx = max(mx, PP - NN)
                                #score = 100000 + PP - NN
                                #score = 100000 + unstand_score

                                #if unstand_score > 150:
                                    #import pdb; pdb.set_trace()
                                #score = 100000 + multivariate_normal.logpdf(Zs, mean=pavg, cov=Spos).sum() - np.log(Zs.size)
                                #score += 
                                #E = 
                            elif 1:
                                M = self.keypoint_mask(mixcomp)
                                md_factor = self.param(0.0)
                                Z = gv.bclip(avgf.ravel(), 0.0000001)
                                    

                                def ev(Z):
                                    #prior = np.dot(B, np.log(Z))*md_factor
                                    zd = np.log(Z) - G_mu1
                                    md = np.sqrt(np.dot(zd, np.linalg.solve(G_Sigma, zd)))
                                    prior = -md * md_factor
                                    #Z = gv.bclip(Z, 0.01)
                                    #   # Not dependent on Z
                                    return -(prior + (X * M * w).sum() + np.log((1 - gv.sigmoid(w * M + gv.logit(Z))) / (1 - Z)).sum())
                                def ev2(Z):
                                    return ev(Z) + ev(bb)

                                #import scipy.optimize as opt
                                #F = avgf.size
                                #print("Optimizing...")
                                ##ret = opt.minimize(ev2, gv.bclip(avgf.ravel(), 0.01), method='L-BFGS-B', bounds=[(0.01, 1-0.01)]*F)

                                std = np.sqrt(np.sum(Z * (1 - Z) * w_kp**2))
                                score = 100000 + np.sum(w_kp * (X_kp - Z)) / std 
                                #score = 100000 - ev(Z)

                            elif 1:
                                md_factor = self.param(0.0)
                                Sreg = S + np.eye(S.shape[0]) * 0.002
                                md = np.sqrt(np.dot(d, np.linalg.solve(Sreg, d)))
                                #md = np.sqrt(np.dot(d, np.dot(invC, d)))

                                bkg = self.fixed_spread_bkg[mixcomp].mean(0).mean(0)

                                # constant factor
                                M = self.keypoint_mask(mixcomp)
                                #C = np.log((1 - gv.sigmoid((w * M) + clogit(avgf))) / (1 - avgf)).sum()

                                c_bkg = gv.bclip(bkg, 0.1)
                                c_avgf = gv.bclip(avgf, 0.1)

                                def clog(x):
                                    return np.log(x.clip(min=0.0001))

                                
                                #def C(z):
                                #    return np.log(1 - 


                                C = clog((1 - gv.sigmoid((w * M) + gv.logit(c_avgf))) / (1 - c_avgf))# / 
                                        #((1 - gv.sigmoid((w * M) + gv.logit(c_bkg))) / (1 - c_bkg)))

                                #C1 = clog((1 - gv.sigmoid((w * M) + gv.logit(c_avgf))) / (1 - c_avgf))
                                #C2 = clog((1 - gv.sigmoid((w * M) + gv.logit(c_bkg))) / (1 - c_bkg))
                                C = C.sum()
                                
                                #score = 100000 + score - md * md_factor# + C
                                std = self.standardization_info[mixcomp]['std']

                                new_score = score - md * md_factor + C / std
                                #new_score = score - md * md_factor
                                #new_score = score + C / std
                                #new_score = C / std
                                score = 100000 + new_score
                            else:
                                score = 100000 + old_score        

                            if FULL:
                                score -= 100000
                                resmap[i,j] = score

                            #score = 100000 + old_score

                            #score = 10000 + alpha * score + (1 - alpha) * old_score

                        if 0 and cascade and 'sturf' in self.extra:
                            #avg = np.apply_over_axes(np.mean, X[2:-2,2:-2], [0, 1]) 
                            sturf = self.extra['sturf'][mixcomp]


                            support = sturf['support']
                            #stds = sturf['stds']
                            #means = sturf['means2']

                            avg = np.apply_over_axes(np.sum, X * support[...,np.newaxis], [0, 1]) / support.sum()

                            diff = sturf['means2'] - sturf['bkg']

                            prior_factor = 50.0

                            prior = np.sum(avg * diff) * prior_factor

                            # Average priors
                            import scipy.stats as st

                    
                            #stds = np.clip(stds, 0.02, np.inf)
                            #stds[:] = 0.05


                            #prior = st.norm.logpdf(avg, loc=sturf['means'], scale=stds).sum() * 0.5
                            #prior = st.norm.logpdf((avg - means) / stds).sum() * 0.1
                            

                            #score = (kern * X).sum() + prior

                            # New weights 
                            #score = 100000 + prior
                            score += 100000 + prior

                            

                        if 0 and cascade and do_cascade and 'svms' in self.extra and score >= cascade_score:
                            # Take the maximum of a neighborhood
                            scores = []
                            Xes = []
                            iis = []
                            jjs = []
                            for ii, jj in itr.product(list(range(max(i-r, 0), min(i+r+1, resmap.shape[0]))), list(range(max(j-r, 0), min(j+r+1, resmap.shape[1])))):
                                # It still has to be greater than the cascade threshold
                                if resmap[ii,jj] >= cascade_score:
                                    thisX = bigger[ii:ii+sh0[0], jj:jj+sh0[1]]

                                    X0 = phi(thisX, mixcomp, self.extra['svms'][mixcomp].get('uses_indices', False))
                                    #X0 = X

                                    #y = self.clfs[mixcomp].predict(X)
                                    #f = self.extra['svms'][mixcomp]['svm'].decision_function([X0]).flat[0]
                                    svm_info = self.extra['svms'][mixcomp]
                                    f = (svm_info['intercept'] + np.sum(svm_info['weights'] * X0)).flat[0]
                                    #fs.append(f)
                                    #score = cascade_score + score - orig_score + 100 / (1 + np.exp(-f/50)).flat[0]
                                    #score = max(score, 100 + f)
                                    scores.append(100 + f + 0.10 * factor)
                                    #scores.append(100 + f)
                                    Xes.append(thisX)
                                    iis.append(ii)
                                    jjs.append(jj)
                                    #score = 100 + f# + 0.001 * factor# + 0.05 * factor# * (1.0 + 0.75 * factor)
                                    #score = cascade_score + score - orig_score + 10 / (1 + np.exp(-Rst/10))

                            besti = np.argmax(scores)
                            score = scores[besti]
                            X = Xes[besti]
                            i = iis[besti]
                            j = jjs[besti]

                        #{{{
                        if 0:
                            if False:
                                from gv.fast import multifeature_correlate2d_with_indices 
                                score = multifeature_correlate2d_with_indices(X, self.weights(mixcomp), self.indices[mixcomp][0])
                                score -= self.standardization_info[mixcomp][0]['mean']
                                score /= self.standardization_info[mixcomp][0]['std']
                                score += 0.75 * factor

                            if cascade and False and 'bkg_mixtures' in self.extra and orig_score >= cascade_score:
                                info = self.extra['bkg_mixtures'][mixcomp]
                                BK = len(info)

                                # Cascade!
                                X = bigger[i:i+sh0[0], j:j+sh0[1]]

                                scores = np.zeros(BK)

                                from gv.fast import multifeature_correlate2d_with_indices 

                                if 1:
                                    # Check backgrounds
                                    for bk in range(BK):
                                        k, b = info[bk]['kern'], info[bk]['bkg']        
                                        k = np.clip(k, self.eps, 1-self.eps)
                                        b = np.clip(b, self.eps, 1-self.eps)
                                        weights = np.log(k / (1 - k) * ((1 - b) / b))

                                        # Does not use keypoints, hmm???
                                        #scores[bk] = (X * np.log(b) + (1 - X) * np.log(1 - b)).sum()
                                        scores[bk] = multifeature_correlate2d_with_indices(X, np.log(b), self.indices[mixcomp][0])[0,0] + \
                                                     multifeature_correlate2d_with_indices(1 - X, np.log(1 - b), self.indices[mixcomp][0])[0,0]

                                    bk = np.argmax(scores)
                                else:
                                    bk = 0
                                k, b = info[bk]['kern'], info[bk]['bkg']        
                                k = np.clip(k, self.eps, 1-self.eps)
                                b = np.clip(b, self.eps, 1-self.eps)
                                weights = np.log(k / (1 - k) * ((1 - b) / b))

                                #R = (X * weights).sum()
                                
                                R = multifeature_correlate2d_with_indices(X, weights, info[bk]['indices'])[0,0]

                                Rst = (R - info[bk]['mean']) / info[bk]['std']

                                # Rescore
                                #score = cascade_score + score - orig_score + 10 / (1 + np.exp(-Rst/10))
                                score = cascade_score + 100 + Rst 
                                #score += 0.75 * factor
                                #score = Rst

                            # Cascade log ratio
                            if self.indices2 is not None and score >= self.extra['bottom_th'][0] and False: 
                                X = bigger[i:i+sh0[0], j:j+sh0[1]]
                                
                                bkg = np.clip(self.fixed_spread_bkg2[mixcomp][0], self.eps, 1 - self.eps)
                                kern = np.clip(self.kernel_templates[mixcomp][0], self.eps, 1 - self.eps)
                                weights = np.log(kern / (1 - kern) * ((1 - bkg) / bkg))

                                from gv.fast import multifeature_correlate2d_with_indices 
                                f = multifeature_correlate2d_with_indices(X, weights, self.indices2[mixcomp])[0,0]

                                score = 0.0 + 1 / (1 + np.exp(-f/100))

                            if cascade and 0 <= score and False:# and score >= self.clfs[mixcomp]['th']: # SECOND CASCADE
                                # Try a local neighborhood !!!!!
                                #rr = 0
                                #score = -np.inf
                                #for ii, jj in itertools.product(xrange(max(0, i - rr), min(bigger.shape[0]-1-sh0[0], i + rr) + 1), \
                                #                                xrange(max(0, j - rr), min(bigger.shape[1]-1-sh0[1], j + rr) + 1)):
                                #    try:
                                        #for i_, j_ in itertools.product(xrange(...), xrange(...)):
                                        #X = bigger[i:i+sh0[0], j:j+sh0[1]]
                                #X = bigger[ii:ii+sh0[0], jj:jj+sh0[1]]
                                X = bigger[i:i+sh0[0], j:j+sh0[1]]
                                
                                #        continue 

                                X0 = phi(X, mixcomp, self.clfs[mixcomp].get('uses_indices', False))

                                #y = self.clfs[mixcomp].predict(X)
                                f = self.clfs[mixcomp][0]['svm'].decision_function([X0])
                                #if f <= 0:
                                    #score = -100
                                #score = f
                                

                                score = 0.0 + 1 / (1 + np.exp(-f)).flat[0]
                                #score = max(score, local_score)
                                #score = f

                                #if y == 0:
                                    #score = -100.0

                                # HERE

                                # Some old code
                                if 0:
                                    #kern = 
                                    
                                    if self.indices is not None:
                                        indices = self.indices[mixcomp][0].astype(np.int32)
                                        from .fast import multifeature_correlate2d_with_indices
                                        res = multifeature_correlate2d_with_indices(X, weights2.astype(np.float64), indices)[0,0]
                                    else:
                                        res = multifeature_correlate2d(X, weights2.astype(np.float64))[0,0]

                                    #info = self.standardization_info[mixcomp][k]
                                    
                                    from .fast import nonparametric_rescore
                                    info = self.standardization_info2[mixcomp][0]
                                    resres = np.zeros((1, 1))
                                    resres[0,0] = res
                                    nonparametric_rescore(resres, info['start'], info['step'], info['points'])
                                
                                    # Update the score! 
                                    score = resres[0,0]
                        #}}}
    
                        conf = score
                        pos = resmap.pos((i, j))
                        #lower = resmap.pos((i + self.boundingboxes2[mixcomp][0]))
                        bb = ((pos[0] * agg_factors2[0] + self.boundingboxes2[mixcomp][0] * agg_factors[0]),
                              (pos[1] * agg_factors2[1] + self.boundingboxes2[mixcomp][1] * agg_factors[1]),
                              (pos[0] * agg_factors2[0] + self.boundingboxes2[mixcomp][2] * agg_factors[0]),
                              (pos[1] * agg_factors2[1] + self.boundingboxes2[mixcomp][3] * agg_factors[1]))

                        index_pos = (i-padding[0], j-padding[1])
        
                        bb = gv.bb.intersection(bb, bb_bigger)
                        #dbb = gv.bb.DetectionBB(score=score, box=bb, index_pos=index_pos, confidence=conf, scale=factor, mixcomp=mixcomp)

                    
                        # full image at img
                        
                        orig_im = None
                        # This will only work correctly when use_padding is False.
                        if False: #if not use_padding:
                            if use_padding:
                                padded_image = ag.util.zeropad(image, image_padding)
                            else:
                                padded_image = image

                            part_size = self.descriptor.settings['part_size']
                            P0 = [i * psize[0], j * psize[1]]
                            P1 = [(i+sh0[0]) * psize[0] + part_size[0] + 3,
                                  (j+sh0[1]) * psize[1] + part_size[1] + 3]



                            P1b = (P0[0] + self.kernel_sizes[mixcomp][0], P0[1] + self.kernel_sizes[mixcomp][1])

                            for i in range(2):
                                if P1b[i] <= image.shape[i]:
                                    P1[i] = P1b[i]
                                else:
                                    diff = P1b[i] - P1[i]
                                    P0[i] -= diff
                                
                            orig_im = padded_image[P0[0]:P1[0], P0[1]:P1[1]]

                        if cascade and do_cascade and score >= cascade_score:
                            save_X = X.copy()
                        else:
                            save_X = None

                        dbb = gv.bb.DetectionBB(score=float(score), 
                                                box=bb, 
                                                index_pos=index_pos, 
                                                confidence=float(conf), 
                                                score0=float(orig_score), 
                                                scale=factor, 
                                                mixcomp=mixcomp, 
                                                bkgcomp=bk, 
                                                img_id=img_id,
                                                X=save_X)#,  KILLS MEMORY CONSUMPTION
                                                #image=orig_im, X=X)

                        if gv.bb.area(bb) > 0:
                            bbs.append(dbb)


        # Let's limit to five per level
        bbs_sorted = self.nonmaximal_suppression(bbs)

        if more_detections:
            bbs_sorted = bbs_sorted[:100]
        else:
            bbs_sorted = bbs_sorted[:15]

        return bbs_sorted, resmap, bkgcomp

    @classmethod
    def build_weights(cls, obj, bkg):
        w = np.log(obj / (1 - obj) * ((1 - bkg) / bkg))
        return w

    @classmethod
    def calc_eps(self, model, settings):
        eps = settings.get('min_probability')
        if eps is None:
            import scipy.stats.mstats as ms
            mult_avg = settings.get('min_probability_mult_avg')
            if mult_avg is not None:
                eps = model.mean() * mult_avg
            else:
                eps = ms.scoreatpercentile(model.ravel(), settings['min_probability_percentile'])
                fallback_eps = settings.get('min_probability_fallback', 0.0001)
                if eps < fallback_eps:
                    eps = fallback_eps
        return eps

    def prepare_eps(self, model):
        self._eps = self.calc_eps(model, self.settings)

    @classmethod
    def build_clipped_weights(cls, obj, bkg, eps):
        clipped_bkg = np.clip(bkg, eps, 1 - eps)
        clipped_obj = np.clip(obj, eps, 1 - eps)
        return cls.build_weights(clipped_obj, clipped_bkg)

    def weights_shape(self, mixcomp):
        return self.weights(mixcomp).shape

    def lrt_weights(self, mixcomp):
        bkg = np.clip(self.fixed_spread_bkg[mixcomp], self.eps, 1 - self.eps)
        kern = np.clip(self.kernel_templates[mixcomp], self.eps, 1 - self.eps)
        #w = np.log(kern / (1 - kern) * ((1 - bkg) / bkg))
        w = self.build_weights(kern, bkg)
        #pd = self.kernel_templates[mixcomp].mean(axis=-1)
        #return w / np.minimum(pd, 0.5)
        return w

    def weights(self, mixcomp):
        if 'weights' in self.extra:
            return self.extra['weights'][mixcomp]
        else:
            return self.lrt_weights(mixcomp)

    def new_weights(self, mixcomp):
        return self.extra['weights'][mixcomp]

    def new_kp_weights(self, mixcomp):
        return self.extra['weights'][mixcomp][tuple(self.indices[mixcomp].T)].reshape((-1, self.num_features))

    def cascade_weights(self, mixcomp, bkgcomp):
        if bkgcomp == -1:
            return self.weights(mixcomp)
        bkg = np.clip(self.extra['bkg_mixtures'][mixcomp][bkgcomp]['bkg'], self.eps, 1 - self.eps)
        kern = np.clip(self.extra['bkg_mixtures'][mixcomp][bkgcomp]['kern'], self.eps, 1 - self.eps)
        return self.build_weights(kern, bkg) 

    def keypoint_weights(self, mixcomp):
        w = self.weights(mixcomp)
        I = self.indices[mixcomp] 
        return w.ravel()[np.array([np.ravel_multi_index(Ii, w.shape) for Ii in I])]

    def keypoint_cascade_weights(self, mixcomp, bkgcomp):
        if bkgcomp == -1:
            return self.keypoint_weights(mixcomp)
        w = self.cascade_weights(mixcomp, bkgcomp)
        I = self.extra['bkg_mixtures'][mixcomp][bkgcomp]['indices']
        return w.ravel()[np.array([np.ravel_multi_index(Ii, w.shape) for Ii in I])]

    def dense_keypoint_weights(self, mixcomp):
        base_weights = self.weights(mixcomp)
        kp_only_weights = np.zeros(base_weights.shape)
        for i, index in enumerate(self.indices[mixcomp]):
            kp_only_weights[tuple(index)] = base_weights[tuple(index)]
        return kp_only_weights

    def keypoint_mask(self, mixcomp):
        kp_only_weights = np.zeros(self.weights_shape(mixcomp))
        for i, index in enumerate(self.indices[mixcomp]):
            kp_only_weights[tuple(index)] = 1 
        return kp_only_weights

    def response_map(self, sub_feats, sub_kernels, spread_bkg, mixcomp, level=0, standardize=True, use_padding=True, strides=(1, 1)):
        if np.min(sub_feats.shape) <= 1:
            return np.zeros((0, 0, 0)), None, None, None


        sh = self.weights_shape(mixcomp)
        pmult = self.settings.get('padding_multiple_of_object', 0.5)
        if not use_padding:
            pmult = 0
        padding = (int(sh[0]*pmult), int(sh[1]*pmult), 0)

        bigger = gv.ndfeature.zeropad(sub_feats, padding)

        # Since the kernel is spread, we need to convert the background
        # model to spread
        radii = self.settings['spread_radii']
        neighborhood_area = ((2*radii[0]+1)*(2*radii[1]+1))

        #spread_bkg = np.clip(spread_bkg, eps, 1 - eps)

        


        if 'weights' in self.extra:
            weights = self.extra['weights'][mixcomp]
        else:
            kern = sub_kernels[mixcomp]
            kern = np.clip(kern, self.eps, 1 - self.eps) 
            if self.settings.get('per_mixcomp_bkg') or True:
                spread_bkg =  spread_bkg[mixcomp]
            spread_bkg = np.clip(spread_bkg, self.eps, 1 - self.eps)
            weights = self.build_weights(kern, spread_bkg)
    
        from .fast import multifeature_correlate2d

        # Make sure the feature vector is big enough
        if bigger.shape[0] <= weights.shape[0] or bigger.shape[1] <= weights.shape[1]:
            return np.zeros((0, 0, 0)), None, None, padding

        if 0:
            res = multifeature_correlate2d(bigger, weights.astype(np.float64))
        else:
            # Randomize some positions
            #randstate = np.random.RandomState(0)
            #NN = 20 
            #indices = np.hstack([randstate.randint(weights.shape[0], size=(NN, 1)), randstate.randint(weights.shape[0], size=(NN, 1))]).astype(np.int32)


            if self.indices is not None and len(self.indices[mixcomp]) > 0:
                indices = self.indices[mixcomp].astype(np.int32)
                from .fast import multifeature_correlate2d_with_indices
                res = multifeature_correlate2d_with_indices(bigger, weights.astype(np.float64), indices, strides=strides)
            else:
                res = multifeature_correlate2d(bigger, weights.astype(np.float64), strides=strides)
    
        lower, upper = gv.ndfeature.inner_frame(bigger, (weights.shape[0]/2, weights.shape[1]/2))
        res = gv.ndfeature(res, lower=lower, upper=upper)

        # Standardization
        if standardize:
            testing_type = self.settings.get('testing_type', 'object-model')
        else:
            testing_type = 'none'

        # TODO: Temporary slow version
        if testing_type == 'NEW':
            neg_llhs = self.standardization_info[mixcomp]['neg_llhs']
            pos_llhs = self.standardization_info[mixcomp]['pos_llhs']

            def logpdf(x, loc=0.0, scale=1.0):
                return -(x - loc)**2 / (2*scale**2) - 0.5 * np.log(2*np.pi) - np.log(scale)

            def score2(R, neg_hist, pos_hist, neg_logs, pos_logs):
                neg_N = 0
                for j, weight in enumerate(neg_hist[0]):
                    if weight > 0:
                        llh = (neg_hist[1][j+1] + neg_hist[1][j]) / 2
                        neg_logs[neg_N] = np.log(weight) + logpdf(R, loc=llh, scale=200)
                        neg_N += 1

                pos_N = 0
                for j, weight in enumerate(pos_hist[0]):
                    if weight > 0:
                        llh = (pos_hist[1][j+1] + pos_hist[1][j]) / 2
                        pos_logs[pos_N] = np.log(weight) + logpdf(R, loc=llh, scale=200)
                        pos_N += 1

                return logsumexp(pos_logs[:pos_N]) - logsumexp(neg_logs[:neg_N])
            
            def score(R, neg_llhs, pos_llhs):
                neg_logs = np.zeros_like(neg_llhs)
                pos_logs = np.zeros_like(pos_llhs)

                for j, llh in enumerate(neg_llhs):
                    neg_logs[j] = logpdf(R, loc=llh, scale=200)

                for j, llh in enumerate(pos_llhs):
                    pos_logs[j] = logpdf(R, loc=llh, scale=200)

                return logsumexp(pos_logs) - logsumexp(neg_logs)

            neg_hist = np.histogram(neg_llhs, bins=10, normed=True)
            pos_hist = np.histogram(pos_llhs, bins=10, normed=True)

            neg_logs = np.zeros_like(neg_hist[0])
            pos_logs = np.zeros_like(pos_hist[0])

            for x, y in itr.product(list(range(res.shape[0])), list(range(res.shape[1]))):
                res[x,y] = score2(res[x,y], neg_hist, pos_hist, neg_logs, pos_logs)

        if testing_type == 'fixed':
            res -= self.standardization_info[mixcomp]['mean']
            res /= self.standardization_info[mixcomp]['std']
            #res -= self.standardization_info[mixcomp]['mean']
            #res /= self.standardization_info[mixcomp]['std']
            #res -= self.standardization_info[mixcomp]['neg_llhs'].mean()
            #res /= self.standardization_info[mixcomp]['neg_llhs'].std()
        elif testing_type == 'non-parametric':
            from .fast import nonparametric_rescore
            info = self.standardization_info[mixcomp]
            nonparametric_rescore(res, info['start'], info['step'], info['points'])
        elif testing_type == 'object-model':
            a = weights
            res -= (kern * a).sum()
            res /= np.sqrt((a**2 * kern * (1 - kern)).sum())
        elif testing_type == 'background-model':
            a = weights
            res -= (spread_bkg * a).sum()
            res /= np.sqrt((a**2 * spread_bkg * (1 - spread_bkg)).sum())
        elif testing_type == 'zero-model':
            pass
        elif testing_type == 'none':
            # We need to add the constant term that isn't included in weights
            res += np.log((1 - kern) / (1 - spread_bkg)).sum() 

        return res, bigger, weights, padding

    def nonmaximal_suppression(self, bbs):
        # This one will respect scales a bit more
        bbs_sorted = sorted(bbs, reverse=True)

        overlap_threshold = self.settings.get('overlap_threshold', 0.5)

        # Suppress within a radius of H neighboring scale factors
        sf = self.settings['scale_factor']
        H = self.settings.get('scale_suppress_radius', 1)
        i = 1
        lo, hi = 1/(H*sf)-0.01, H*sf+0.01
        while i < len(bbs_sorted):
            # TODO: This can be vastly improved performance-wise
            area_i = gv.bb.area(bbs_sorted[i].box)
            for j in range(i):
                # VERY TEMPORARY: This avoids suppression between classes
                #if bbs_sorted[i].mixcomp != bbs_sorted[j].mixcomp:
                   #continue
        
                overlap = gv.bb.area(gv.bb.intersection(bbs_sorted[i].box, bbs_sorted[j].box))/area_i
                scale_diff = (bbs_sorted[i].scale / bbs_sorted[j].scale)

                if overlap > overlap_threshold and lo <= scale_diff <= hi: 
                    del bbs_sorted[i]
                    i -= 1
                    break

            i += 1
        return bbs_sorted

    def bounding_box_for_mix_comp(self, k):
        """This returns a bounding box of the support for a given component"""

        # Take the bounding box of the support, with a certain threshold.
        if self.support is not None:
            supp = self.support[k] 
            supp_axs = [supp.max(axis=1-i) for i in range(2)]

            th = self.settings['bounding_box_opacity_threshold']
            # Check first and last value of that threshold
            bb = [np.where(supp_axs[i] > th)[0][[0,-1]] for i in range(2)]

            # This bb looks like [(x0, x1), (y0, y1)], when we want it as (x0, y0, x1, y1)
            psize = self.settings['subsample_size']
            ret = (bb[0][0]/psize[0], bb[1][0]/psize[1], bb[0][1]/psize[0], bb[1][1]/psize[1])

            return ret
        else:
            psize = self.settings['subsample_size']
            return (0, 0, self.settings['image_size'][0]/psize[0], self.settings['image_size'][1]/psize[1])

    #@classmethod
    #def bounding_box_from_mean_alphas(cls, support, threshold

    def bounding_box_for_mix_comp2(self, k):
        """This returns a bounding box of the support for a given component"""

        #psize = self.settings['subsample_size']
        psize = self.descriptor.subsample_size

        # Take the bounding box of the support, with a certain threshold.
        if 'bbs' in self.extra:
            bb = self.extra['bbs'][k]
             
            image_size = self.settings['image_size']
            ret = ((bb[0] - image_size[0]//2)/psize[0], (bb[1] - image_size[1]//2)/psize[1], (bb[2] - image_size[0]//2)/psize[0], (bb[3] - image_size[1]//2)/psize[1])

        elif self.support is not None:
            supp = self.support[k] 
            supp_axs = [supp.max(axis=1-i) for i in range(2)]
            th = self.settings['bounding_box_opacity_threshold']

            # Check first and last value of that threshold
            bb = [np.where(supp_axs[i] > th)[0][[0,-1]] for i in range(2)]

            # This bb looks like [(x0, x1), (y0, y1)], when we want it as (x0, y0, x1, y1)
            psize = self.descriptor.subsample_size
            ret = ((bb[0][0] - supp.shape[0]//2)/psize[0], (bb[1][0] - supp.shape[1]//2)/psize[1], (bb[0][1] - supp.shape[0]//2)/psize[0], (bb[1][1] - supp.shape[1]//2)/psize[1])

            # TODO: SO TEMP!
            #inflate = 1
            #ret = (ret[0] - inflate, ret[1] - inflate, ret[2] + inflate, ret[3] + inflate)
        else:
            psize = self.descriptor.subsample_size
            size = (self.orig_kernel_size[0]/psize[0], self.orig_kernel_size[1]/psize[1])
            ret = (-size[0]/2, -size[1]/2, size[0]/2, size[1]/2)

        ret = gv.bb.inflate2(ret, np.true_divide(self.settings.get('inflate_bounding_box', 0), psize))
        return ret

    @property
    def orig_kernel_size(self):
        return self.settings['image_size']

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
                    score = gv.bb.fraction_metric(bb1, bb2)
                    if score >= 0.5:
                        if best_score is None or score > best_score:
                            best_score = score
                            best_bbobj = bb1obj 
                            best_bb = bb1

            if best_bbobj is not None:
                bb2obj.correct = True
                bb2obj.difficult = best_bbobj.difficult
                bb2obj.overlap = best_score
                # Don't count difficult
                if not best_bbobj.difficult:
                    tot += 1
                used_bb.add(best_bb)

    @property
    def eps(self):
        return self._eps


    def _preprocess(self):
        """Pre-processes things"""

        self.descriptor.settings['subsample_size'] = self.settings['subsample_size']
        self.descriptor.settings['spread_radii'] = self.settings['spread_radii']
        
        # Prepare bounding boxes for all mixture model
        self.boundingboxes = np.array([self.bounding_box_for_mix_comp(i) for i in range(self.num_mixtures)])
        self.boundingboxes2 = np.array([self.bounding_box_for_mix_comp2(i) for i in range(self.num_mixtures)])

    @classmethod
    def load_from_dict(cls, d):
        try:
            num_mixtures = d['num_mixtures']
            descriptor_cls = cls.DESCRIPTOR.getclass(d['descriptor_name'])
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
            #obj.orig_kernel_size = d.get('orig_kernel_size')
            obj.kernel_basis = d.get('kernel_basis')
            obj.kernel_basis_samples = d.get('kernel_basis_samples')
            obj.kernel_templates = d.get('kernel_templates')
            obj.kernel_sizes = d.get('kernel_sizes')
            obj.use_alpha = d['use_alpha']
            obj.support = d.get('support')
            obj.bkg_mixture_params = d.get('bkg_mixture_params')

            obj.fixed_bkg = d.get('fixed_bkg')
            obj.fixed_spread_bkg = d.get('fixed_spread_bkg')
            obj.fixed_spread_bkg2 = d.get('fixed_spread_bkg2') # TODO: New

            obj.prepare_eps(obj.fixed_spread_bkg[0])

            obj.standardization_info = d.get('standardization_info')
            obj.standardization_info2 = d.get('standardization_info2')
            obj.clfs = d.get('clfs')
    
            obj.indices = d.get('indices')
            obj.indices2 = d.get('indices2')

            obj.extra = d.get('extra')

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
        #d['orig_kernel_size'] = self.orig_kernel_size
        d['kernel_templates'] = self.kernel_templates
        d['kernel_basis'] = self.kernel_basis
        d['kernel_basis_samples'] = self.kernel_basis_samples
        d['kernel_sizes'] = self.kernel_sizes
        d['use_alpha'] = self.use_alpha
        d['support'] = self.support
        d['settings'] = self.settings
        d['bkg_mixture_params'] = self.bkg_mixture_params

        if self.fixed_bkg is not None:
            d['fixed_bkg'] = self.fixed_bkg

        if self.fixed_spread_bkg is not None:
            d['fixed_spread_bkg'] = self.fixed_spread_bkg 

        if self.fixed_spread_bkg2 is not None:
            d['fixed_spread_bkg2'] = self.fixed_spread_bkg2
    
        if self.standardization_info is not None:
            d['standardization_info'] = self.standardization_info

        if self.standardization_info2 is not None:
            d['standardization_info2'] = self.standardization_info2

        if self.clfs is not None:
            d['clfs'] = self.clfs

        if self.indices is not None:
            d['indices'] = self.indices

        if self.indices2 is not None:
            d['indices2'] = self.indices2

        d['extra'] = self.extra

        return d
