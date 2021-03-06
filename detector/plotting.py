from __future__ import division
import gv
import numpy as np
import matplotlib.pylab as plt

def plot_image(fileobj, filename=None, show_corrects=False, bare=False):
    img = gv.img.load_image(fileobj.path)

    fig = plt.figure()
    ax = fig.add_subplot(111)
    s = dict(cmap=plt.cm.gray) if img.ndim == 2 else {} 
    ax.imshow(img, **s)
    if bare:
        ax.set_xticks([])
        ax.set_yticks([])

    for bbobj in fileobj.boxes:
        bb = bbobj.box
        if show_corrects:
            if bbobj.correct:
                color = 'lightgreen'
            else:
                color = 'red' 
        else:
            if bbobj.difficult:
                color = 'red'
            else:
                color = 'lightgreen'
        plt.gca().add_patch(plt.Rectangle((bb[1], bb[0]), bb[3]-bb[1], bb[2]-bb[0], facecolor='none', edgecolor=color, linewidth=2.0))
        if not bare:
            plt.text(bb[1], bb[0], "{0:.2f}".format(bbobj.confidence), color='white', size=6, ha='left', va='bottom')
        #plt.gca().add_patch(plt.Rectangle((bb[0], bb[1]), bb[2]-bb[0], bb[3]-bb[1], facecolor='none', edgecolor='lightgreen', linewidth=2.0))
    if not bare:    
        ax.set_title("img_id = {0}".format(fileobj.img_id))
    else:
        plt.tight_layout()

    if filename is not None:
        plt.savefig(filename)
    else:
        plt.show()


def plot_results(detector, img, x, small, mixcomp=None, bounding_boxes=[], img_resized=None):
    # Get max peak
    #print ix, iy

    #print '---'
    #print x.shape
    #print small.shape

    plt.clf()
    if small is None and x is None:
        plt.subplot(111)
    else:
        plt.subplot(121)
    plt.title('Input image')
    plt.imshow(img, cmap=plt.cm.gray)

    for dbb in bounding_boxes[::-1]:
        bb = dbb.box
        color = 'cyan' if dbb.correct else 'red'
        plt.gca().add_patch(plt.Rectangle((bb[1], bb[0]), bb[3]-bb[1], bb[2]-bb[0], facecolor='none', edgecolor=color, linewidth=2.0))
        #plt.text(bb[1], bb[0], "{0:.2f}".format(dbb.confidence), color='white', backgroundcolor=color, size=8, ha='left', va='bottom')
        plt.text(bb[1], bb[0], "{0:.2f}".format(dbb.confidence), color='yellow', size=6, ha='left', va='bottom')

    if x is not None:
        plt.subplot(122)
        #plt.title('Response map ({:.2f}, {:.2f})'.format(float(x.min()), float(x.max())))
        plt.title('Response map')
        plt.imshow(x, interpolation='nearest')#, vmin=-40000, vmax=-36000)
        #plt.colorbar()

    if 0:
        if small is not None:
            plt.subplot(223)
            plt.title('Feature activity')
            plt.imshow(small.sum(axis=-1), interpolation='nearest')
            plt.colorbar()

        if img_resized is not None:
            plt.subplot(224)
            plt.title('Resized image')
            plt.imshow(img_resized, interpolation='nearest', cmap=plt.cm.gray)
        
    if 0:
        pass
        plt.title('Normalized stuff')
        plt.imshow(x / np.clip(small.sum(axis=-1), 5, np.inf), interpolation='nearest')
        plt.colorbar()
    else:
        #if mixcomp is not None:
            #plt.title('Kernel Bernoulli probability averages')
            #plt.imshow(detector.kernels[mixcomp].mean(axis=-1), interpolation='nearest', cmap=plt.cm.RdBu, vmin=0, vmax=1)
        #plt.colorbar()
        pass

def plot_box(bb, color='lightgreen'):
    plt.gca().add_patch(plt.Rectangle((bb[1], bb[0]), bb[3]-bb[1], bb[2]-bb[0], facecolor='none', edgecolor=color, linewidth=2.0))
