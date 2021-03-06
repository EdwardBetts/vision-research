#import gv.collection
from collections import namedtuple
import amitgroup.util
from functools import partial

ImgFile = namedtuple('ImgFile', ['path', 'boxes', 'img_id', 'img_size'])
#ImgFile.__new__ = partial(ImgFile.__new__, path=None, boxes=[], img_id='(none)')
ImgFile.__new__.__defaults__ = (None, [], '(none)', None)

def contests():
    return ('voc-val', 'voc-train', 'voc-trainval', 'voc-test', 'voc-profile', 'voc-profile2', 'voc-profile3', 'voc-profile4', 'voc-profile5', 
            'voc-easy', 'voc-fronts', 'voc-fronts-negs', 'voc-sides', 'voc-val-profile', 'voc-test-profile',
            'uiuc', 'uiuc-multiscale', 'rot-uiuc', 'rot360-uiuc',
            'inria-test',
            'custom-cad-profile', 'custom-cad-all', 'custom-cad-all-shuffled', 'custom-tmp-frontbacks',
            'voc-traingen')

def datasets():
    return ('none', 'voc', 'uiuc', 'uiuc-multiscale', 'rot-uiuc', 'rot360-uiuc', 'inria',
            'custom-cad-profile', 'custom-cad-all', 'custom-cad-all-shuffled', 'custom-tmp-frontbacks',
            'voc-traingen')

def load_files(contest, obj_class):
    import gv.voc
    if contest == 'voc-train':
        files, tot = gv.voc.load_files(obj_class, dataset='train')
    elif contest == 'voc-val':
        files, tot = gv.voc.load_files(obj_class, dataset='val')
    elif contest == 'voc-val-profile':
        files, tot = gv.voc.load_files(obj_class, dataset='val', poses={'Left', 'Right'})
    elif contest == 'voc-trainval':
        files, tot = gv.voc.load_files(obj_class, dataset='trainval')
    elif contest == 'voc-test':
        files, tot = gv.voc.load_files(obj_class, dataset='test')
    elif contest == 'voc-test-profile':
        files, tot = gv.voc.load_files(obj_class, dataset='test', poses={'Left', 'Right'})
    elif contest == 'voc-profile':
        files, tot = gv.voc.load_files(obj_class, dataset='profile')
    elif contest == 'voc-profile2':
        files, tot = gv.voc.load_files(obj_class, dataset='profile2')
    elif contest == 'voc-profile3':
        files, tot = gv.voc.load_files(obj_class, dataset='profile3')
    elif contest == 'voc-profile4':
        files, tot = gv.voc.load_files(obj_class, dataset='profile4')
    elif contest == 'voc-profile5':
        files, tot = gv.voc.load_files(obj_class, dataset='profile5')
    elif contest == 'voc-easy':
        files, tot = gv.voc.load_files(obj_class, dataset='easy')
    elif contest == 'voc-fronts':
        files, tot = gv.voc.load_files(obj_class, dataset='fronts')
    elif contest == 'voc-fronts-negs':
        files, tot = gv.voc.load_files(obj_class, dataset='fronts-negs')
    elif contest == 'voc-sides':
        files, tot = gv.voc.load_files(obj_class, dataset='sides')
    elif contest == 'uiuc':
        files, tot = gv.uiuc.load_testing_files(anno_format='single')
    elif contest == 'uiuc-multiscale':
        files, tot = gv.uiuc.load_testing_files(anno_format='scale')
    elif contest == 'rot-uiuc':
        files, tot = gv.uiuc.load_testing_files(anno_format='single', env='ROT_UIUC_DIR')
    elif contest == 'rot360-uiuc':
        files, tot = gv.uiuc.load_testing_files(anno_format='free', env='ROT360_UIUC_DIR')
    elif contest == 'inria-test' or contest == 'inria':
        assert obj_class == 'person', "INRIA only has person class"
        files, tot = gv.inria.load_files(obj_class, dataset='test')
    elif contest.startswith('custom'):
        name = contest[len('custom-'):]
        files, tot = gv.custom.load_testing_files(name)
    elif contest == 'voc-traingen':
        files, tot = gv.voc.load_files(obj_class, dataset='traingen')
    else:
        raise ValueError("Contest does not exist: {0}".format(contest))
    return files, tot

def load_file(contest, img_id, obj_class=None, path=None):
    import gv.voc
    import gv.custom
    if contest.startswith('uiuc'):
        return gv.uiuc.load_testing_file(img_id, anno_format={'uiuc': 'single', 'uiuc-multiscale': 'scale'}[contest])
    if contest.startswith('rot-uiuc'):
        return gv.uiuc.load_testing_file(img_id, anno_format='single', env='ROT_UIUC_DIR')
    if contest.startswith('rot360-uiuc'):
        return gv.uiuc.load_testing_file(img_id, anno_format='free', env='ROT360_UIUC_DIR')
    elif contest.startswith('voc'):
        return gv.voc.load_file(obj_class, img_id) 
    elif contest == 'inria':
        return gv.inria.load_file('person', img_id) 
    elif contest.startswith('custom'):
        name = contest[len('custom-'):]
        return gv.custom.load_testing_file(name, img_id)
    elif contests == 'none':
        assert path is not None 
        return ImgFile(path=path, boxes=[], img_id=-1, img_size=None)

# This function could leave an edge if the image is not big enough
def extract_image_from_bbobj(bbobj, detector, contest, obj_class, kernel_shape):
    img_id = bbobj.img_id
    #img_id = det['img_id']
    fileobj = load_file(contest, img_id, obj_class=obj_class)

    im = gv.img.load_image(fileobj.path) 
    im = gv.img.asgray(im)
    im = gv.img.resize_with_factor_new(im, 1/bbobj.scale)

    #kern = kernels[k][m]
    #bkg = all_bkg[k][m]
    #kern = np.clip(kern, eps, 1 - eps)
    #bkg = np.clip(bkg, eps, 1 - eps)

    d0, d1 = kernel_shape #kern.shape[:2] 

    psize = detector.settings['subsample_size']
    radii = detector.settings['spread_radii']
    
    feats = detector.descriptor.extract_features(im, dict(spread_radii=radii, subsample_size=psize, preserve_size=False))

    i0, j0 = bbobj.index_pos
    pad = max(-min(0, i0), -min(0, j0), max(0, i0+d0 - feats.shape[0]), max(0, j0+d1 - feats.shape[1]))

    feats = amitgroup.util.zeropad(feats, (pad, pad, 0))
    X = feats[pad+i0:pad+i0+d0, pad+j0:pad+j0+d1]
    return X 
     


def extract_features_from_bbobj(bbobj, detector, contest, obj_class, kernel_shape):
    """
    Retrieves features from a DetectionBB object 
    """
    #bb = (det['top'], det['left'], det['bottom'], det['right'])
    #k = det['mixcomp']
    #m = det['bkgcomp']
    #bbobj = gv.bb.DetectionBB(bb, score=det['confidence'], confidence=det['confidence'], mixcomp=k, correct=det['correct'])

    img_id = bbobj.img_id
    #img_id = det['img_id']
    fileobj = load_file(contest, img_id, obj_class=obj_class)

    im = gv.img.load_image(fileobj.path) 
    im = gv.img.asgray(im)
    im = gv.img.resize_with_factor_new(im, 1/bbobj.scale)

    #kern = kernels[k][m]
    #bkg = all_bkg[k][m]
    #kern = np.clip(kern, eps, 1 - eps)
    #bkg = np.clip(bkg, eps, 1 - eps)

    d0, d1 = kernel_shape #kern.shape[:2] 

    psize = detector.settings['subsample_size']
    radii = detector.settings['spread_radii']
    
    feats = detector.descriptor.extract_features(im, dict(spread_radii=radii, subsample_size=psize, preserve_size=False))

    i0, j0 = bbobj.index_pos
    pad = max(-min(0, i0), -min(0, j0), max(0, i0+d0 - feats.shape[0]), max(0, j0+d1 - feats.shape[1]))

    feats = amitgroup.util.zeropad(feats, (pad, pad, 0))
    X = feats[pad+i0:pad+i0+d0, pad+j0:pad+j0+d1]
    return X 
