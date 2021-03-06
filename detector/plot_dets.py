from __future__ import division
from __future__ import print_function

import numpy as np
import matplotlib.pylab as plt
import matplotlib.gridspec as gridspec

def plot_detection_histograms(detections, detector, score_name='confidence', output_file=None):
    mixcomps = detections['mixcomp'].max() + 1

    adjust = 0
    if score_name == 'confidence2':
        score_name = 'confidence'
        adjust = -100

    #bins = np.arange(0.0, 0.2, 0.01)
    if score_name == 'confidence':
        bins = np.arange(-20, 20, 0.5)
    else:
        bins = np.arange(-8, 20, 0.5)

    # Automatically determine bins
    mn, mx = detections['confidence'].min(), detections['confidence'].max()
    bins = np.linspace(mn, mx, 50)

    tps_fps = np.zeros((mixcomps, 2))

    fig = plt.figure(figsize=(15, mixcomps))

    fractions_N = 4
    fractions_N = 0

    #gs = gridspec.GridSpec(mixcomps, 2, width_ratios=[7,1])
    from mpl_toolkits.axes_grid1 import Grid
    gs = gridspec.GridSpec(mixcomps, 6 + fractions_N)
    gs.update(wspace=0.0, hspace=0.0)
    #gs = Grid(fig, rect=111, nrows_ncols=(mixcomps, 6 + fractions_N), label_mode='L')
    for mixcomp in xrange(mixcomps):
        mydets = detections[detections['mixcomp'] == mixcomp]

        tps_fps[mixcomp,0] = mydets[mydets['correct'] == 0].size
        tps_fps[mixcomp,1] = mydets[mydets['correct'] == 1].size

        ax = plt.subplot(gs[mixcomps-1-mixcomp,:5])
        #plt.subplot(gs[2*mixcomp])
        plt.hist(mydets[mydets['correct'] == 0][score_name] + adjust, label='FP', bins=bins, alpha=0.5, normed=True)
        print(mixcomp, "mean:", np.mean(mydets[mydets['correct'] == 0][score_name]))
        if len(mydets[mydets['correct'] == 1]):
            plt.hist(mydets[mydets['correct'] == 1][score_name] + adjust, label='TP', bins=bins, alpha=0.5, normed=True)
        ax.set_xlim((bins[0], bins[-1]))
        ax.set_ylim((0, 0.3))
        #plt.ylim((0, 25))

        if mixcomp == 0:
            ax.set_xlabel('Score')
            #ax.set_ylabel('Frequency')
        else:
            ax.set_xticklabels([])

        ax.set_yticklabels([])         

        if mixcomp == mixcomps-1:
            ax.set_title('Score histograms')
        else:
            ax.title.set_visible(False)
            #ax.set_xticks([])

        #plt.plot(r, p, label=str(mixcomp))
        #plt.xlabel('Recall')
        #plt.ylabel('Precision')
        #plt.xlim((0, 1))
        #plt.ylim((0, 1))

        #plt.subplot(gs[2*mixcomp+1]).set_axis_off()
        axr = plt.subplot(gs[mixcomps-1-mixcomp,5])
        axr.set_axis_off()
        axr.imshow(detector.support[mixcomp], cmap=plt.cm.gray)

    # Plot which one

    plt.legend()
    #plt.tight_layout()
    #plt.show()

    ind = np.arange(mixcomps)

    if 0:
        width = 0.35
        ax = plt.subplot(gs[:,6:])
        ax.barh(ind, tps_fps[:,0], height=width, color='b', alpha=0.5, label='FP')
        ax.barh(ind+width, tps_fps[:,1], height=width, color='g', alpha=0.5, label='TP')
        plt.xlabel('Count')
        plt.title('TP/FP ratios')
        plt.legend()
    if output_file is not None:
        plt.savefig(output_file)
        plt.close()
    else:
        plt.show()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Test response of model')
    parser.add_argument('model', metavar='<model file>', type=argparse.FileType('rb'), help='Filename of model file')
    parser.add_argument('results', metavar='<file>', nargs=1, type=argparse.FileType('rb'), help='Filename of results file')

    args = parser.parse_args()
    model_file = args.model
    results_file = args.results[0]

    import gv

    detector = gv.Detector.load(model_file)
    data = np.load(results_file)

    #p, r = data['precisions'], data['recalls']
    detections = data['detections']

    plot_detection_histograms(detections, detector)
