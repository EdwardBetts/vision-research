from __future__ import division
import argparse

parser = argparse.ArgumentParser(description='Test response of model')
parser.add_argument('settings', metavar='<settings file>', type=argparse.FileType('r'), help='Filename of settings file')
parser.add_argument('model', metavar='<model file>', type=argparse.FileType('rb'), help='Filename of model file')
parser.add_argument('output', metavar='<output file>', type=argparse.FileType('wb'), help='Filename of output model file')

args = parser.parse_args()
settings_file = args.settings
model_file = args.model
output_file = args.output

import matplotlib
matplotlib.use("Agg")
import matplotlib.pylab as plt
import numpy as np
import gv
import amitgroup as ag
import glob
from skimage.transform import pyramid_reduce, pyramid_expand
from settings import load_settings

settings = load_settings(settings_file)
detector = gv.Detector.load(model_file)
descriptor = detector.descriptor

def create_bkg_generator(size, files):
    i = 0
    prng = np.random.RandomState(0)
    yielded = 0
    while True:
        im = gv.img.asgray(gv.img.load_image(files[i]))
        to = [im.shape[i]-size[i]+1 for i in xrange(2)]
        #print min(to)
        if min(to) > 0:
            x, y = [prng.randint(0, to[i]) for i in xrange(2)]
            yielded += 1
            yield im[x:x+size[0],y:y+size[1]]
        i += 1
        if i == len(files):
            assert yielded == 0, 'Background images probably too small!'
            i = 0
         
# Iterate through the original CAD images. Superimposed them onto random background
neg_files = sorted(glob.glob(settings['detector']['neg_dir']))
cad_files = sorted(glob.glob(settings['detector']['train_dir']))[:settings['detector'].get('train_limit')]

size = settings['detector']['image_size']

bkg_generator = create_bkg_generator(size, neg_files)

# First iterate through the background images to 
def limit(gen, N):
    #print 'limiting'
    for i in xrange(N):
        #print i
        yield gen.next() 

pi = np.zeros(descriptor.num_parts)
pi_spread = np.zeros(descriptor.num_parts)
tot = 0
tot_spread = 0
cut = 4
radii = settings['detector']['spread_radii']
subsize = settings['detector']['subsample_size']

#print 'Checking background model'

for bkg in limit(create_bkg_generator(size, neg_files), len(cad_files) * 5):
#for bkg in create_bkg_generator(size, neg_files)
    #print bkg.shape
    feats = descriptor.extract_features(bkg, settings=dict(spread_radii=(0, 0), subsample_size=(1, 1)))
    x = np.rollaxis(feats[cut:-cut,cut:-cut], 2).reshape((descriptor.num_parts, -1))
    tot += x.shape[1]
    pi += x.sum(axis=1)

    feats_spread = descriptor.extract_features(bkg, settings=dict(spread_radii=radii, subsample_size=(1, 1)))
    x_spread = np.rollaxis(feats_spread[cut:-cut,cut:-cut], 2).reshape((descriptor.num_parts, -1))
    tot_spread += x_spread.shape[1]
    pi_spread += x_spread.sum(axis=1)

unspread_bkg = pi / tot
spread_bkg_obs = pi_spread / tot_spread

spread_bkg = 1 - (1 - unspread_bkg)**((2*radii[0]+1)*(2*radii[1]+1))

if 0:
    plt.clf()
    plt.hist(np.log(np.clip(spread_bkg,1e-7, np.inf))/np.log(10), 30)
    plt.savefig("spread-hist.png")

print 'unspread_bkg.mean() = ', unspread_bkg.mean()
print 'spread_bkg_obs.mean() = ', spread_bkg_obs.mean()
print 'translated.mean() = ', spread_bkg.mean()

#unspread_bkg = not None 

# Create model
print 'Creating model'

#a = 1 - unspread_bkg.sum()
#bkg_categorical = np.concatenate(([a], unspread_bkg))

#C = detector.kernel_basis * np.expand_dims(bkg_categorical, -1)
#kernels = C.sum(axis=-2) / detector.kernel_basis_samples.reshape((-1,) + (1,)*(C.ndim-2))

eps = settings['detector']['min_probability']
unspread_eps = 1 - (1 - eps)**(1/((2*radii[0]+1)*(2*radii[1]+1)))

#import pdb; pdb.set_trace()

#import pdb; pdb.set_trace()

unspread_bkg = np.clip(unspread_bkg, unspread_eps, 1-unspread_eps)

# Set fixed

if 0:
    unspread_bkg2 = np.ones(unspread_bkg.size) * settings['detector']['fixed_background_probability']

    #kernels = np.clip(kernels, 1e-5, 1-1e-5)
    kernels = detector.prepare_kernels(unspread_bkg2, settings=dict(min_probability=0.005))#, settings=settings['detector'])

    kernels *= unspread_bkg / unspread_bkg2

    kernels = np.clip(kernels, 0.005, 1-0.005)
else:
    kernels = detector.prepare_kernels(unspread_bkg, settings=dict(min_probability=0.005))#, settings=settings['detector'])

spread_bkg = np.clip(spread_bkg, eps, 1 - eps)

if 0:
    spread_bkg = kernels[0,0,0]
    spread_bkg = 1 - (1 - unspread_bkg)**((2*radii[0]+1)*(2*radii[1]+1))
    eps = settings['detector']['min_probability']
    #eps = 1e-10 
    spread_bkg = np.clip(spread_bkg, eps, 1 - eps)
    spread_bkg_obs = np.clip(spread_bkg_obs, eps, 1 - eps)

    diff = spread_bkg_obs / spread_bkg

    import pdb; pdb.set_trace()
    # Now, try correcting the kernels
    kernels *= diff
    spread_bkg = spread_bkg_obs

comps = detector.mixture.mixture_components()

#theta = kernels.reshape((kernels.shape[0], -1))

llhs = [[] for i in xrange(detector.num_mixtures)] 

print 'Iterating CAD images'

for cad_i, cad_filename in enumerate(cad_files):
    cad = gv.img.load_image(cad_filename)
    f = cad.shape[0]/size[0]
    if f > 1:
        cad = pyramid_reduce(cad, downscale=f)
    elif f < 1:
        cad = pyramid_expand(cad, upscale=1/f)
    mixcomp = comps[cad_i]

    alpha = cad[...,3]
    gray_cad = gv.img.asgray(cad)
    bkg_img = bkg_generator.next()
    
    # Notice, gray_cad is not multiplied by alpha, since most files use premultiplied alpha 
    composite = gray_cad + bkg_img * (1 - alpha)

    # Get features
    X_full = descriptor.extract_features(composite, settings=dict(spread_radii=radii))

    X = gv.sub.subsample(X_full, subsize)


    a = np.log(kernels[mixcomp]/(1-kernels[mixcomp]) * ((1-spread_bkg)/spread_bkg))

    # Check log-likelihood
    llh = np.sum(X * a)
    llhs[mixcomp].append(llh)
    
detector.fixed_train_mean = np.asarray([np.mean(llhs[k]) for k in xrange(detector.num_mixtures)]) 
detector.fixed_train_std = np.asarray([np.std(llhs[k]) for k in xrange(detector.num_mixtures)])
detector.kernel_templates = kernels
detector.kernel_basis = None

if 1:
    detector.fixed_bkg = unspread_bkg
    detector.fixed_spread_bkg = spread_bkg
    detector.settings['bkg_type'] = 'from-file'
else:
    detector.settings['fixed_bkg'] = 0.03
    detector.settings['bkg_type'] = 'constant'

detector.settings['testing_type'] = 'fixed'
#detector.settings['train_unspread'] = False
detector.settings['kernels_ready'] = True

detector.save(output_file)
