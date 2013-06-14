from __future__ import division
import matplotlib
matplotlib.use('Agg')
import matplotlib.pylab as plt
import glob
import numpy as np
import amitgroup as ag
import gv
import os
import sys
from itertools import product, cycle
from superimpose_experiment import generate_random_patches

def generate_random_patches(filenames, size, seed=0, per_image=1):
    randgen = np.random.RandomState(seed)
    failures = 0
    for fn in cycle(filenames):
        #img = gv.img.resize_with_factor_new(gv.img.asgray(gv.img.load_image(fn)), randgen.uniform(0.5, 1.0))
        img = gv.img.asgray(gv.img.load_image(fn))

        for l in xrange(per_image):
            # Random position
            x_to = img.shape[0]-size[0]+1
            y_to = img.shape[1]-size[1]+1

            if x_to >= 1 and y_to >= 1:
                x = randgen.randint(x_to) 
                y = randgen.randint(y_to)
                yield img[x:x+size[0], y:y+size[1]]
                failures = 0
            else:
                failures += 1

            # The images are too small, let's stop iterating
            if failures >= 30:
                return

def fetch_bkg_model(settings, neg_files):
    randgen = np.random.RandomState(0)

    size = settings['detector']['image_size']
    descriptor = gv.load_descriptor(settings)

    radii = settings['detector']['spread_radii']
    psize = settings['detector']['subsample_size']
    cb = settings['detector'].get('crop_border')

    counts = np.zeros(descriptor.num_features)
    tot = 0

    for fn in neg_files[:200]:
        ag.info('Processing {0} for background model extraction'.format(fn))

        im = gv.img.resize_with_factor_new(gv.img.asgray(gv.img.load_image(fn)), randgen.uniform(0.5, 1.0))

        feats = descriptor.extract_features(im, settings=dict(spread_radii=radii, crop_border=cb))
        subfeats = gv.sub.subsample(feats, psize)
        x = np.rollaxis(subfeats, 2).reshape((descriptor.num_features, -1))
    
        tot += x.shape[1]
        counts += x.sum(axis=1)
        
    return counts / tot 
    

def _create_kernel_for_mixcomp(mixcomp, settings, bb, indices, files, neg_files):
    im_size = settings['detector']['image_size']
    size = gv.bb.size(bb)
    orig_size = size
    
    gen = generate_random_patches(neg_files, size, seed=mixcomp)
    descriptor = gv.load_descriptor(settings)

    eps = settings['detector']['min_probability']
    radii = settings['detector']['spread_radii']
    psize = settings['detector']['subsample_size']
    duplicates = settings['detector'].get('duplicates', 1)
    cb = settings['detector'].get('crop_border')

    kern = None
    total = 0

    alpha_cum = None

    for index in indices: 
        ag.info("Processing image of index {0} and mixture component {1}".format(index, mixcomp))
        gray_im, alpha = _load_cad_image(files[index], im_size, bb)

        bin_alpha = alpha > 0.05

        if alpha_cum is None:
            alpha_cum = bin_alpha.astype(np.uint32)
        else:
            alpha_cum += bin_alpha 

        for dup in xrange(duplicates):
            neg_im = gen.next()
            superimposed_im = neg_im * (1 - alpha) + gray_im * alpha

            gv.img.save_image(superimposed_im, 'foutput/img-{0}.png'.format(np.random.randint(10000)))

            feats = descriptor.extract_features(superimposed_im, settings=dict(spread_radii=radii, crop_border=cb))
            feats = gv.sub.subsample(feats, psize)

            if kern is None:
                kern = feats.astype(np.uint32)
            else:
                kern += feats

            total += 1
    
    kern = kern.astype(np.float64) / total 
    kern = np.clip(kern, eps, 1-eps)

    support = alpha_cum.astype(np.float64) / len(indices)

    #kernels.append(kern)
    return kern, orig_size, support 

def _create_kernel_for_mixcomp_star(args):
    return _create_kernel_for_mixcomp(*args)

def _load_cad_image(fn, im_size, bb):
    im = gv.img.load_image(fn)
    im = gv.img.resize(im, im_size)
    im = gv.img.crop_to_bounding_box(im, bb)
    gray_im, alpha = gv.img.asgray(im), im[...,3] 
    return gray_im, alpha
        
def _calc_standardization_for_mixcomp(mixcomp, settings, bb, kern, bkg, indices, files, neg_files):
    im_size = settings['detector']['image_size']
    size = gv.bb.size(bb)

    # Use the same seed for all mixture components! That will make them easier to compare,
    # without having to sample to infinity.
    gen = generate_random_patches(neg_files, size, seed=0)
    descriptor = gv.load_descriptor(settings)

    eps = settings['detector']['min_probability']
    radii = settings['detector']['spread_radii']
    psize = settings['detector']['subsample_size']
    duplicates = settings['detector'].get('duplicates', 1) * 10 
    cb = settings['detector'].get('crop_border')

    total = 0

    llhs = []

    weights = np.log(kern / (1 - kern) * ((1 - bkg) / bkg))

    for index in indices: 
        ag.info("Standardizing image of index {0} and mixture component {1}".format(index, mixcomp))
        gray_im, alpha = _load_cad_image(files[index], im_size, bb)
        for dup in xrange(duplicates):
            neg_im = gen.next()
            #superimposed_im = neg_im * (1 - alpha) + gray_im * alpha
            superimposed_im = neg_im

            feats = descriptor.extract_features(superimposed_im, settings=dict(spread_radii=radii, crop_border=cb))
            feats = gv.sub.subsample(feats, psize)

            llh = (weights * feats).sum()
            llhs.append(llh)

    np.save('llhs-{0}.npy'.format(mixcomp), llhs)

    return np.mean(llhs), np.std(llhs)

def _calc_standardization_for_mixcomp_star(args):
    return _calc_standardization_for_mixcomp(*args)

def superimposed_model(settings, threading=True):
    offset = settings['detector'].get('train_offset', 0)
    limit = settings['detector'].get('train_limit')
    num_mixtures = settings['detector']['num_mixtures']
    assert limit is not None, "Must specify limit in the settings file"
    files = sorted(glob.glob(settings['detector']['train_dir']))[offset:offset+limit]
    neg_files = sorted(glob.glob(settings['detector']['neg_dir']))

    # Train a mixture model to get a clustering of the angles of the object
    descriptor = gv.load_descriptor(settings)
    detector = gv.Detector(num_mixtures, descriptor, settings['detector'])
    detector.train_from_images(files)

    comps = detector.mixture.mixture_components()
    each_mix_N = np.bincount(comps, minlength=num_mixtures)

    for fn in glob.glob('toutputs/*.png'):
        os.remove(fn)

    from shutil import copyfile
    for mixcomp in xrange(detector.num_mixtures):
        indices = np.where(comps == mixcomp)[0]
        for i in indices:
            copyfile(files[i], 'toutputs/mixcomp-{0}-index-{1}.png'.format(mixcomp, i))

    support = detector.support 

    kernels = []

    #print "TODO, quitting"
    #return detector

    psize = settings['detector']['subsample_size']

    def get_full_size_bb(k):
        bb = detector.bounding_box_for_mix_comp(k)
        return tuple(bb[i] * psize[i%2] for i in xrange(4))

    def iround(x):
        return int(round(x))

    def make_bb(bb, max_bb):
        # First, make it integral
        bb = (iround(bb[0]), iround(bb[1]), iround(bb[2]), iround(bb[3]))
        bb = gv.bb.inflate(bb, 2)
        bb = gv.bb.intersection(bb, max_bb)
        return bb

    max_bb = (0, 0) + detector.settings['image_size']
    bbs = [make_bb(get_full_size_bb(k), max_bb) for k in xrange(detector.num_mixtures)]

    #for mixcomp in xrange(num_mixtures):
    
    if threading:
        from multiprocessing import Pool
        p = Pool(7)
        # Order is important, so we can't use imap_unordered
        imapf = p.imap
    else:
        from itertools import imap as imapf
    
    argses = [(i, settings, bbs[i], list(np.where(comps == i)[0]), files, neg_files) for i in xrange(detector.num_mixtures)] 
    #argses = [(i,) for i in xrange(detector.num_mixtures)] 
    kernels = []
    orig_sizes = []
    new_support = []
    for kern, orig_size, sup in imapf(_create_kernel_for_mixcomp_star, argses):
        kernels.append(kern)
        orig_sizes.append(orig_size)
        new_support.append(sup)

    detector.kernel_templates = kernels
    detector.kernel_sizes = orig_sizes
    detector.settings['kernel_ready'] = True
    detector.use_alpha = False
    detector.support = new_support

    # Determine the background
    ag.info("Determining background")
    #spread_bkg = np.mean([kern[:2].reshape((-1, kern.shape[-1])).mean(axis=0) for kern in kernels], axis=0)
    #spread_bkg = np.mean([kern.reshape((-1, kern.shape[-1])).mean(axis=0) for kern in kernels], axis=0)
    #spread_bkg = kernels[0][1].mean(axis=0)
    spread_bkg = fetch_bkg_model(settings, neg_files)

    eps = detector.settings['min_probability']
    spread_bkg = np.clip(spread_bkg, eps, 1 - eps)

    print 'spread_bkg shape:', spread_bkg.shape
    detector.fixed_bkg = None # Not needed, since kernel_ready is True
    detector.fixed_spread_bkg = spread_bkg
    detector.settings['bkg_type'] = 'from-file'

    # Determine the standardization values
    ag.info("Determining standardization values")

    detector.fixed_train_mean = np.zeros(detector.num_mixtures)
    detector.fixed_train_std = np.ones(detector.num_mixtures)
    
    argses = [(i, settings, bbs[i], kernels[i], spread_bkg, list(np.where(comps == i)[0]), files, neg_files) for i in xrange(detector.num_mixtures)]
    for i, (mean, std) in enumerate(imapf(_calc_standardization_for_mixcomp_star, argses)):
        detector.fixed_train_mean[i] = mean
        detector.fixed_train_std[i] = std

    detector.settings['testing_type'] = 'fixed'

    return detector 

    if 0:
        if threading:
            from multiprocessing import Pool
            p = Pool(7)
            # Important to run imap, since otherwise we will accumulate too
            # much memory, since the count structure is quite big.
            imapf = p.imap_unordered
        else:
            from itertools import imap as imapf

        argses = [(settings, files[i], comps[i]) for i in xrange(len(files))] 

        all_counts = imapf(_process_file_star, argses)
    

if __name__ == '__main__':
    import argparse
    from settings import load_settings
   
    ag.set_verbose(True)
    
    parser = argparse.ArgumentParser(description="Convert model to integrate background model")
    parser.add_argument('settings', metavar='<settings file>', type=argparse.FileType('r'), help='Filename of settings file')
    parser.add_argument('output', metavar='<output file>', type=argparse.FileType('wb'), help='Model output file')
    parser.add_argument('--no-threading', action='store_true', default=False, help='Turn off threading')

    args = parser.parse_args()
    settings_file = args.settings
    output_file = args.output
    threading = not args.no_threading

    settings = load_settings(settings_file)

    detector = superimposed_model(settings, threading=threading)

    #detector = gv.Detector(settings['detector']['num_mixtures'], descriptor, settings['detector'])
    #detector.kernel_templates = 

    detector.save(output_file)