

from settings import argparse_settings
sett = argparse_settings("Train detector")
dsettings = sett['detector']


#import argparse

#parser = argparse.ArgumentParser(description='Train mixture model on edge data')
#parser.add_argument('patches', metavar='<patches file>', type=argparse.FileType('rb'), help='Filename of patches file')
#parser.add_argument('model', metavar='<output model file>', type=argparse.FileType('wb'), help='Filename of the output models file')
#parser.add_argument('mixtures', metavar='<number mixtures>', type=int, help='Number of mixture components')
#parser.add_argument('--use-voc', action='store_true', help="Use VOC data to train model")

import gv
import glob
import os
import os.path
import amitgroup as ag

ag.set_verbose(True)

#descriptor = gv.load_descriptor(gv.BinaryDetector.DESCRIPTOR, sett)
descriptor = gv.load_descriptor(sett)
detector = gv.BernoulliDetector(dsettings['num_mixtures'], descriptor, dsettings)

if dsettings['use_voc']:
    files = gv.voc.load_object_images_of_size(sett['voc'], 'bicycle', dsettings['image_size'], dataset='train')
else:
    base_path = ''
    if 'base_path' in dsettings:
        base_path = os.environ[dsettings['base_path']]
    path = os.path.join(base_path, dsettings['train_dir'])
    files = sorted(glob.glob(path))
    # TEMP!
    from random import shuffle
    shuffle(files)

limit = dsettings.get('train_limit')
if limit is not None:
    files = files[:limit]

print "Training on {0} files".format(len(files))
#files = files[:10]

detector.train_from_images(files)

detector.save(dsettings['file'])

